# Client.py
from tkinter import *
from tkinter import ttk
import tkinter.messagebox
from PIL import Image, ImageTk
import socket, threading, sys, traceback, os, io, select, time
from collections import deque

from RtpPacket import RtpPacket

CACHE_FILE_NAME = "cache-"
CACHE_FILE_EXT = ".jpg"
TARGET_WIDTH = 640
TARGET_HEIGHT = 360


class Client:
    INIT = 0
    READY = 1
    PLAYING = 2
    state = INIT

    SETUP = "SETUP"
    PLAY = "PLAY"
    PAUSE = "PAUSE"
    TEARDOWN = "TEARDOWN"

    def __init__(self, master, serveraddr, serverport, rtpport, filename):
        self.hdMode = False
        # frameParts dùng cho HD: frameNum -> {"total": int, "chunks": {idx: bytes}}
        self.frameParts = {}
        # tổng số frame (server gửi trong header Frames: N, nếu có)
        self.totalFrames = 0

        # buffer thật cho các frame JPEG đã lắp xong, đợi render
        # mỗi phần tử là (frameNum, jpeg_bytes)
        self.frameQueue = deque()
        self.bufferLock = threading.Lock()
        self.framesDisplayed = 0  # số frame đã hiển thị

        self.master = master
        self.master.protocol("WM_DELETE_WINDOW", self.handler)
        self.createWidgets()

        self.serverAddr = serveraddr
        self.serverPort = int(serverport)
        self.rtpPort = int(rtpport)
        self.rtpPort2 = self.rtpPort + 2  # secondary port for song song RTP
        self.fileName = filename

        self.rtspSeq = 0
        self.sessionId = 0
        self.requestSent = -1
        self.teardownAcked = 0

        # thread & event điều khiển play/pause/teardown
        self.playEvent = None       # sẽ tạo khi Play
        self.rtpThread = None       # thread nhận RTP
        self.renderThread = None    # thread render frame

        self.connectToServer()
        self.frameNbr = 0  # frame lớn nhất đã hiển thị

    def createWidgets(self):
        """Build GUI."""
        self.hdButton = Button(self.master, width=20, padx=3, pady=3)
        self.hdButton["command"] = self.toggleHD
        self.hdButton["text"] = "HD Mode: OFF"
        self.hdButton.grid(row=1, column=4, padx=2, pady=2)

        # Styles cho progress bars (nếu sau này dùng ttk.Progressbar)
        self.style = ttk.Style()
        self.style.theme_use("default")
        self.style.configure(
            "Red.Horizontal.TProgressbar",
            troughcolor="#1f1f1f",
            background="#e74c3c",
            bordercolor="#1f1f1f",
            lightcolor="#e74c3c",
            darkcolor="#c0392b",
        )
        self.style.configure(
            "Green.Horizontal.TProgressbar",
            troughcolor="#1f1f1f",
            background="#0ae724",
            bordercolor="#1f1f1f",
            lightcolor="#0ae724",
            darkcolor="#0ae724",
        )

        self.setup = Button(self.master, width=20, padx=3, pady=3)
        self.setup["text"] = "Setup"
        self.setup["command"] = self.setupMovie
        self.setup.grid(row=1, column=0, padx=2, pady=2)

        self.start = Button(self.master, width=20, padx=3, pady=3)
        self.start["text"] = "Play"
        self.start["command"] = self.playMovie
        self.start.grid(row=1, column=1, padx=2, pady=2)

        self.pause = Button(self.master, width=20, padx=3, pady=3)
        self.pause["text"] = "Pause"
        self.pause["command"] = self.pauseMovie
        self.pause.grid(row=1, column=2, padx=2, pady=2)

        self.teardown = Button(self.master, width=20, padx=3, pady=3)
        self.teardown["text"] = "Teardown"
        self.teardown["command"] = self.exitClient
        self.teardown.grid(row=1, column=3, padx=2, pady=2)

        # Canvas hiển thị video
        self.canvas = Canvas(
            self.master,
            width=TARGET_WIDTH,
            height=TARGET_HEIGHT,
            bg="black",
            highlightthickness=0,
        )
        self.canvas.bind("<Configure>", self._onCanvasResize)
        self.canvas_image_id = None
        self.canvas.grid(
            row=0, column=0, columnspan=5, sticky=W + E + N + S, padx=5, pady=5
        )

        # Progress (cache vs live)
        self.progressHeight = 16
        self.progressFrame = Frame(
            self.master, height=self.progressHeight, width=TARGET_WIDTH
        )
        self.progressFrame.grid(
            row=2, column=0, columnspan=5, sticky=W + E, padx=5, pady=4
        )
        self.progressFrame.grid_propagate(False)
        self.progressCanvas = Canvas(
            self.progressFrame,
            width=TARGET_WIDTH,
            height=self.progressHeight,
            bg="#1a1a1a",
            highlightthickness=0,
            bd=0,
        )
        self.progressCanvas.place(relx=0, rely=0, relwidth=1, relheight=1)
        self.cacheBarId = self.progressCanvas.create_rectangle(
            0, 0, 0, self.progressHeight, fill="#e74c3c", width=0
        )
        self.liveBarId = self.progressCanvas.create_rectangle(
            0, 0, 0, self.progressHeight, fill="#0ae724", width=0
        )

        # Status text
        self.statusVar = StringVar(value="")
        self.statusLabel = Label(self.master, textvariable=self.statusVar, fg="green")
        self.statusLabel.grid(
            row=3, column=0, columnspan=5, sticky=W, padx=5, pady=2
        )

        self.cacheActive = False

    def toggleHD(self):
        """Toggle HD mode on/off."""
        self.hdMode = not self.hdMode
        if self.hdMode:
            self.hdButton["text"] = "HD Mode: ON"
            print("HD Mode is ON")
        else:
            self.hdButton["text"] = "HD Mode: OFF"
            print("HD Mode is OFF")

    def setupMovie(self):
        """Setup button handler."""
        if self.state == self.INIT:
            self.sendRtspRequest(self.SETUP)

    def exitClient(self):
        """Teardown button handler."""
        # dừng render & recv nếu đang chạy
        if self.playEvent:
            self.playEvent.set()

        if self.cacheActive:
            self.stopCachingIndicator(False)

        self.sendRtspRequest(self.TEARDOWN)

        # đóng GUI
        try:
            self.master.destroy()
        except:
            pass

        # xóa file cache nếu có
        cache_path = CACHE_FILE_NAME + str(self.sessionId) + CACHE_FILE_EXT
        if os.path.exists(cache_path):
            try:
                os.remove(cache_path)
            except:
                pass

    def pauseMovie(self):
        """Pause button handler."""
        if self.state == self.PLAYING:
            if self.cacheActive:
                self.stopCachingIndicator(False)
            # báo cho render/recv dừng
            if self.playEvent:
                self.playEvent.set()
            self.sendRtspRequest(self.PAUSE)

    def playMovie(self):
        """Play button handler."""
        if self.state == self.READY:
            # event để báo dừng cho các thread
            self.playEvent = threading.Event()
            self.playEvent.clear()

            # reset buffer và trạng thái hiển thị
            with self.bufferLock:
                self.frameQueue.clear()
            self.frameNbr = 0
            self.framesDisplayed = 0

            self.startCachingIndicator()

            # Thread nhận RTP (1 thread listen nhiều socket)
            self.rtpThread = threading.Thread(target=self.listenRtp, daemon=True)
            self.rtpThread.start()

            # Thread render frame từ buffer ở tốc độ cố định
            self.renderThread = threading.Thread(
                target=self.renderLoop, daemon=True
            )
            self.renderThread.start()

            # Gửi lệnh PLAY đến server
            self.sendRtspRequest(self.PLAY)

    # ==============================
    # VÒNG LẶP NHẬN RTP + LẮP FRAME
    # ==============================
    def listenRtp(self):
        """Listen for RTP packets from all RTP sockets and assemble frames (SD/HD)."""

        self.currentFrameBuffer = bytearray()  # cho SD mode
        self.expectedFrameNbr = 1

        while True:
            if self.playEvent and self.playEvent.is_set():
                break

            sockets = getattr(self, "rtpSockets", [])
            if not sockets:
                time.sleep(0.01)
                continue

            try:
                # chờ socket nào đọc được (tối đa 50ms)
                readable, _, _ = select.select(sockets, [], [], 0.05)
            except Exception:
                continue

            for sock in readable:
                try:
                    data = sock.recv(20480)
                except socket.timeout:
                    continue
                except Exception:
                    continue

                if not data:
                    continue

                try:
                    rtpPacket = RtpPacket()
                    rtpPacket.decode(data)

                    currFrameNbr = rtpPacket.seqNum()
                    payload = rtpPacket.getPayload()
                    marker = rtpPacket.marker()
                except Exception as exc:
                    print("Bad RTP packet:", exc)
                    continue

                # SD mode (mỗi frame 1 hoặc vài gói, marker bit kết thúc frame)
                if not self.hdMode:
                    # nếu bị nhảy frame → drop frame cũ đang dở
                    if currFrameNbr != self.expectedFrameNbr:
                        if len(self.currentFrameBuffer) > 0:
                            print(
                                f"Dropping incomplete SD frame {self.expectedFrameNbr} (jump to {currFrameNbr})"
                            )
                        self.currentFrameBuffer = bytearray()
                        self.expectedFrameNbr = currFrameNbr

                    self.currentFrameBuffer.extend(payload)

                    if marker:  # kết thúc frame
                        self.flushFrame(currFrameNbr, self.currentFrameBuffer)
                        self.currentFrameBuffer = bytearray()
                        self.expectedFrameNbr = currFrameNbr + 1
                else:
                    # HD mode: mỗi frame có nhiều chunk, handleHdPayload tự assemble
                    self.handleHdPayload(currFrameNbr, payload, marker)

        # khi thoát vòng lặp, đóng các socket RTP
        for s in getattr(self, "rtpSockets", []):
            try:
                s.close()
            except:
                pass

    # =====================
    # VÒNG LẶP RENDER FRAME
    # =====================
    def renderLoop(self):
        """Render frames from buffer at ~30fps."""
        target_interval = 1 / 30.0  # 30 fps
        last_time = time.time()

        while True:
            if self.playEvent and self.playEvent.is_set():
                break

            frame = None
            with self.bufferLock:
                if self.frameQueue:
                    frame = self.frameQueue.popleft()

            if frame is not None:
                frameNum, jpegBytes = frame
                try:
                    photo = self.buildPhoto(jpegBytes)
                    self.frameNbr = max(self.frameNbr, frameNum)
                    self.framesDisplayed = max(self.framesDisplayed, frameNum)
                    self.updateMovie(photo)
                except Exception as exc:
                    print(f"Cannot decode/display frame {frameNum}:", exc)

                # duy trì tốc độ ~30fps
                now = time.time()
                elapsed = now - last_time
                sleep_time = max(0, target_interval - elapsed)
                if sleep_time > 0:
                    time.sleep(sleep_time)
                last_time = time.time()
            else:
                # không có frame nào trong buffer → nghỉ 5ms
                time.sleep(0.005)

    # xác định frame trong buffer là JPEG hợp lệ và đẩy vào frameQueue
    def flushFrame(self, frameNum, frameBytes):
        """Check JPEG markers then push frame into real buffer queue."""
        if (
            len(frameBytes) >= 4
            and frameBytes[:2] == b"\xff\xd8"
            and frameBytes[-2:] == b"\xff\xd9"
        ):
            with self.bufferLock:
                self.frameQueue.append((frameNum, bytes(frameBytes)))
        else:
            print(f"Incomplete JPEG for frame {frameNum}, skipped")

    # chuyển jpeg bytes thành PhotoImage để hiển thị
    def buildPhoto(self, data):
        """Build a PhotoImage from raw JPEG bytes (avoid disk IO for speed)."""
        canvas_w = getattr(
            self, "canvas_width", self.canvas.winfo_width() or TARGET_WIDTH
        )
        canvas_h = getattr(
            self, "canvas_height", self.canvas.winfo_height() or TARGET_HEIGHT
        )
        canvas_w = max(1, canvas_w)
        canvas_h = max(1, canvas_h)

        img = Image.open(io.BytesIO(data)).convert("RGB")
        # Preserve aspect ratio and letterbox into the target frame
        src_w, src_h = img.size
        scale = min(canvas_w / src_w, canvas_h / src_h)
        new_w, new_h = max(1, int(src_w * scale)), max(1, int(src_h * scale))
        img = img.resize(
            (new_w, new_h),
            getattr(Image, "Resampling", Image).LANCZOS,
        )

        # tạo nền đen và paste ảnh vào giữa
        canvas_img = Image.new("RGB", (canvas_w, canvas_h), "black")
        offset = ((canvas_w - new_w) // 2, (canvas_h - new_h) // 2)
        canvas_img.paste(img, offset)
        return ImageTk.PhotoImage(canvas_img)

    # ghép nhiều gói payload thành khung hình HD hoàn chỉnh
    def handleHdPayload(self, frameNum, payload, markerBit):
        """Reassemble HD frame from chunked payloads using the embedded chunk index/total."""
        if len(payload) < 4:
            print(f"HD payload too short for frame {frameNum}, dropped")
            return

        chunk_idx = int.from_bytes(payload[0:2], "big")
        total_chunks = int.from_bytes(payload[2:4], "big")
        chunk_data = payload[4:]

        # lưu tất cả các chunk vào frameParts
        frame_entry = self.frameParts.setdefault(
            frameNum, {"total": total_chunks, "chunks": {}}
        )
        frame_entry["total"] = total_chunks  # refresh in case of late arrival
        frame_entry["chunks"][chunk_idx] = chunk_data

        # Nếu đã đủ toàn bộ chunk -> assemble
        if len(frame_entry["chunks"]) == frame_entry["total"]:
            ordered = []
            for idx in range(frame_entry["total"]):
                part = frame_entry["chunks"].get(idx)
                if part is None:
                    print(
                        f"Missing chunk {idx}/{frame_entry['total']} for frame {frameNum}, cannot assemble yet"
                    )
                    return
                ordered.append(part)

            full_frame = b"".join(ordered)
            self.flushFrame(frameNum, full_frame)
            self.frameParts.pop(frameNum, None)
        elif markerBit:
            # marker đã đến nhưng vẫn thiếu chunk -> drop frame để tránh kẹt
            missing_indices = [
                i for i in range(frame_entry["total"]) if i not in frame_entry["chunks"]
            ]
            print(
                f"Dropping HD frame {frameNum} (missing chunks {missing_indices})"
            )
            self.frameParts.pop(frameNum, None)

    # vẽ hình lên canvas
    def updateMovie(self, photo):
        """Update the image as video frame in the GUI."""
        center_x = getattr(
            self, "canvas_width", self.canvas.winfo_width() or TARGET_WIDTH
        ) // 2
        center_y = getattr(
            self, "canvas_height", self.canvas.winfo_height() or TARGET_HEIGHT
        ) // 2

        if self.canvas_image_id is None:
            self.canvas_image_id = self.canvas.create_image(
                center_x, center_y, image=photo
            )
        else:
            self.canvas.itemconfig(self.canvas_image_id, image=photo)
            self.canvas.coords(self.canvas_image_id, center_x, center_y)

        self.canvas.image = photo  # keep reference to avoid GC

        # Cập nhật thanh tiến trình (dựa trên frame đã hiển thị + buffer thật)
        self.updateProgress(self.frameNbr)

    def _onCanvasResize(self, event):
        """Keep track of canvas size and re-center the current frame when resized."""
        self.canvas_width = event.width
        self.canvas_height = event.height
        if self.canvas_image_id is not None:
            self.canvas.coords(
                self.canvas_image_id, event.width // 2, event.height // 2
            )

    def connectToServer(self):
        """Connect to the Server. Start a new RTSP/TCP session."""
        self.rtspSocket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            self.rtspSocket.connect((self.serverAddr, self.serverPort))
        except:
            tkinter.messagebox.showwarning(
                "Connection Failed",
                "Connection to '%s' failed." % self.serverAddr,
            )

    def sendRtspRequest(self, requestCode):
        """Send RTSP request to the server."""
        print(f"sendRtspRequest: {requestCode}")
        if requestCode == self.SETUP and self.state == self.INIT:
            threading.Thread(target=self.recvRtspReply, daemon=True).start()
            self.rtspSeq += 1
            request = (
                f"{self.SETUP} {self.fileName} RTSP/1.0\n"
                f"CSeq: {self.rtspSeq}\n"
                f"Transport: RTP/UDP; client_port= {self.rtpPort}"
            )
            request += "\n Prefer: HD" if self.hdMode else "\n Prefer: SD"
            self.requestSent = self.SETUP

        elif requestCode == self.PLAY and self.state == self.READY:
            self.rtspSeq += 1
            request = (
                f"{self.PLAY} {self.fileName} RTSP/1.0\n"
                f"CSeq: {self.rtspSeq}\n"
                f"Session: {self.sessionId}"
            )
            self.requestSent = self.PLAY

        elif requestCode == self.PAUSE and self.state == self.PLAYING:
            self.rtspSeq += 1
            request = (
                f"{self.PAUSE} {self.fileName} RTSP/1.0\n"
                f"CSeq: {self.rtspSeq}\n"
                f"Session: {self.sessionId}"
            )
            self.requestSent = self.PAUSE

        elif requestCode == self.TEARDOWN and not self.state == self.INIT:
            self.rtspSeq += 1
            request = (
                f"{self.TEARDOWN} {self.fileName} RTSP/1.0\n"
                f"CSeq: {self.rtspSeq}\n"
                f"Session: {self.sessionId}"
            )
            self.requestSent = self.TEARDOWN
        else:
            return

        self.rtspSocket.send(request.encode())
        print("\nData sent:\n" + request)

    def recvRtspReply(self):
        """Receive RTSP reply from the server."""
        while True:
            reply = self.rtspSocket.recv(1024)
            if reply:
                self.parseRtspReply(reply.decode("utf-8"))

            if self.requestSent == self.TEARDOWN:
                try:
                    self.rtspSocket.shutdown(socket.SHUT_RDWR)
                except:
                    pass
                try:
                    self.rtspSocket.close()
                except:
                    pass
                break

    def parseRtspReply(self, data):
        """Parse the RTSP reply from the server."""
        lines = data.split("\n")
        if len(lines) < 3:
            return

        try:
            seqNum = int(lines[1].split(" ")[1])
        except:
            return

        if seqNum == self.rtspSeq:
            try:
                session = int(lines[2].split(" ")[1])
            except:
                return

            if self.sessionId == 0:
                self.sessionId = session

            if self.sessionId == session:
                # Optional total frames header
                for line in lines[3:]:
                    if line.startswith("Frames"):
                        try:
                            self.totalFrames = int(line.split(" ")[1])
                        except:
                            pass

                # Kiểm tra status code
                try:
                    code = int(lines[0].split(" ")[1])
                except:
                    return

                if code == 200:
                    if self.requestSent == self.SETUP:
                        self.state = self.READY
                        self.openRtpPort()
                        self.statusVar.set("Ready to play")
                    elif self.requestSent == self.PLAY:
                        self.state = self.PLAYING
                        self.statusVar.set("")
                    elif self.requestSent == self.PAUSE:
                        self.state = self.READY
                        if self.playEvent:
                            self.playEvent.set()
                        self.statusVar.set("Ready to play")
                    elif self.requestSent == self.TEARDOWN:
                        self.state = self.INIT
                        self.teardownAcked = 1
                        self.statusVar.set("")

    def openRtpPort(self):
        """Open RTP sockets bound to specified ports."""
        self.rtpSockets = []
        for port in (self.rtpPort, self.rtpPort2):
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1 << 20)
            except Exception as exc:
                print("Cannot set SO_RCVBUF:", exc)
            sock.settimeout(0.01)
            try:
                sock.bind(("", port))
            except:
                tkinter.messagebox.showwarning(
                    "Unable to Bind", "Unable to bind PORT=%d" % port
                )
                continue
            self.rtpSockets.append(sock)

    def updateProgress(self, frameNum):
        """Update progress indicator based on displayed frame and real buffer."""
        try:
            width = self.progressCanvas.winfo_width() or TARGET_WIDTH
            height = self.progressHeight

            # số frame đang trong buffer
            with self.bufferLock:
                buffer_len = len(self.frameQueue)

            if self.totalFrames > 0:
                max_val = max(1, self.totalFrames)
                live_val = min(frameNum, max_val)
                # cache thật = live + số frame đang trong queue
                cache_val = min(live_val + buffer_len, max_val)
                live_frac = live_val / max_val
                cache_frac = cache_val / max_val
            else:
                # nếu không biết tổng frame, hiển thị tương đối
                live_val = frameNum
                live_frac = (live_val % 5000) / 5000.0
                cache_frac = min(1.0, live_frac + min(0.2, buffer_len / 50.0))

            self.progressCanvas.coords(
                self.cacheBarId, 0, 0, width * cache_frac, height
            )
            self.progressCanvas.coords(
                self.liveBarId, 0, 0, width * live_frac, height
            )
        except Exception as exc:
            print("Cannot update progress bars:", exc)

    def startCachingIndicator(self):
        """Start caching indicator (now based on real buffer)."""
        self.cacheActive = True

    def stopCachingIndicator(self, success=False):
        """Stop caching indicator."""
        self.cacheActive = False

    def handler(self):
        """Handler on explicitly closing the GUI window."""
        self.pauseMovie()
        if tkinter.messagebox.askokcancel(
            "Quit?", "Are you sure you want to quit?"
        ):
            self.exitClient()
        else:
            # nếu người dùng chọn ở lại, resume play
            self.playMovie()
