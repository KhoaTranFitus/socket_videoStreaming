# Client.py
from tkinter import *
import tkinter.messagebox
import socket, threading, io, time

from RtpPacket import RtpPacket
from hd_handler import HDHandler
from cache_manager import CacheManager
from renderer import Renderer

TARGET_WIDTH = 640
TARGET_HEIGHT = 360


class Client:
    # RTSP states
    INIT = 0
    READY = 1
    PLAYING = 2

    # RTSP commands
    SETUP = "SETUP"
    PLAY = "PLAY"
    PAUSE = "PAUSE"
    TEARDOWN = "TEARDOWN"

    def __init__(self, master, serveraddr, serverport, rtpport, filename):
        self.master = master
        self.master.protocol("WM_DELETE_WINDOW", self.handler)

        # network info
        self.serverAddr = serveraddr
        self.serverPort = int(serverport)
        self.rtpPort = int(rtpport)
        self.rtpPort2 = self.rtpPort + 2
        self.fileName = filename

        # RTSP parameters
        self.rtspSeq = 0
        self.sessionId = 0
        self.state = self.INIT
        self.requestSent = -1
        
        self.isPaused = False
        self.maxCacheSize = 2000  # frames (increased for buffering)

        # playback state
        self.frameNbr = 0
        self.totalFrames = 0
 
        self.latestReceivedFrame = 0
        self.bufferWarmed = False
        self.playerRunning = False

        # modules
        self.hdMode = False
        self.hd = HDHandler()
        self.cache = CacheManager(max_size=self.maxCacheSize)        
        # GUI        
        # GUI
        self.createWidgets()

        # Renderer module
        self.renderer = Renderer(self.canvas, TARGET_WIDTH, TARGET_HEIGHT)
        self.canvas.bind("<Configure>", self.renderer.on_resize)

        # RTSP TCP socket
        self.connectToServer()

    # ============================================================
    # GUI
    # ============================================================

    def createWidgets(self):
        # control buttons
        self.setupB = Button(self.master, width=20, text="Setup", command=self.setupMovie)
        self.setupB.grid(row=1, column=0, padx=2, pady=2)

        self.playB = Button(self.master, width=20, text="Play", command=self.playMovie)
        self.playB.grid(row=1, column=1, padx=2, pady=2)

        self.pauseB = Button(self.master, width=20, text="Pause", command=self.pauseMovie)
        self.pauseB.grid(row=1, column=2, padx=2, pady=2)

        self.teardownB = Button(self.master, width=20, text="Teardown", command=self.exitClient)
        self.teardownB.grid(row=1, column=3, padx=2, pady=2)

        # HD toggle
        self.hdButton = Button(self.master, width=20, text="HD Mode: OFF", command=self.toggleHD)
        self.hdButton.grid(row=1, column=4, padx=2, pady=2)

        # video canvas
        self.canvas = Canvas(self.master, width=TARGET_WIDTH, height=TARGET_HEIGHT,
                             bg="black", highlightthickness=0)
        self.canvas.grid(row=0, column=0, columnspan=5, padx=5, pady=5)

        # progress bar (cache/live)
        self.progressHeight = 16
        self.progressFrame = Frame(self.master, height=self.progressHeight)
        self.progressFrame.grid(row=2, column=0, columnspan=5, sticky="we", padx=5, pady=4)
        self.progressFrame.grid_propagate(False)

        self.progressCanvas = Canvas(self.progressFrame, height=self.progressHeight,
                                     bg="#1a1a1a", highlightthickness=0)
        self.progressCanvas.place(relx=0, rely=0, relwidth=1, relheight=1)

        self.cacheBarId = self.progressCanvas.create_rectangle(0, 0, 0, self.progressHeight,
                                                               fill="#e74c3c", width=0)
        self.liveBarId = self.progressCanvas.create_rectangle(0, 0, 0, self.progressHeight,
                                                              fill="#00ff00", width=0)

    # ============================================================
    # BUTTON HANDLERS
    # ============================================================
    def get_state_text(self):
        if self.state == self.INIT:
            return "INIT"
        if self.state == self.READY:
            return "READY"
        if self.state == self.PLAYING:
            return "PLAYING"
        return "UNKNOWN"

    def toggleHD(self):
        self.hdMode = not self.hdMode
        self.hdButton["text"] = "HD Mode: ON" if self.hdMode else "HD Mode: OFF"
        print("[Client] HD Mode =", self.hdMode)

    def setupMovie(self):
        print(f"[STATE] Setup button pressed. State = {self.get_state_text()}")

        if self.state == self.INIT:
            self.sendRtspRequest(self.SETUP)

    def playMovie(self):
        print(f"[STATE] Play button pressed. State = {self.get_state_text()}")

        if self.isPaused:
            self.isPaused = False

        if self.state == self.READY:
            self.playEvent = threading.Event()
            self.isPaused = False
            # start RTP listeners

            if not hasattr(self, "rtpThreads"):
                self.rtpThreads = []
                for sock in self.rtpSockets:
                    t = threading.Thread(target=self.listenRtp, args=(sock,))
                    t.daemon = True
                    t.start()
                    self.rtpThreads.append(t)

            self.sendRtspRequest(self.PLAY)
            if not self.playerRunning:
                self.startPlayerLoop()

    def pauseMovie(self):
        print(f"[STATE] Pause button pressed. State = {self.get_state_text()}")
        # Only pause locally, let RTP threads continue buffering until limit
        self.isPaused = True

    def exitClient(self):
        print(f"[STATE] Teardown button pressed. State = {self.get_state_text()}")
        if self.state != self.INIT:
            self.sendRtspRequest(self.TEARDOWN)
        if hasattr(self, "playEvent"):
            self.playEvent.set()
        
        self.playerRunning = False
        self.master.destroy()
        self.rtspSocket.close()

    def handler(self):
        self.exitClient()

    # ============================================================
    # RTSP CONNECTION
    # ============================================================

    def connectToServer(self):
        self.rtspSocket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            self.rtspSocket.connect((self.serverAddr, self.serverPort))
        except:
            tkinter.messagebox.showwarning("Connection Failed",
                                           f"Cannot connect to {self.serverAddr}")

    # ============================================================
    # SEND RTSP
    # ============================================================

    def sendRtspRequest(self, code):
        print(f"[RTSP] Sending {code}")

        self.rtspSeq += 1
        request = ""

        if code == self.SETUP and self.state == self.INIT:
            request = (
                f"SETUP {self.fileName} RTSP/1.0\n"
                f"CSeq: {self.rtspSeq}\n"
                f"Transport: RTP/UDP; client_port= {self.rtpPort}\n"
                f"Prefer: {'HD' if self.hdMode else 'SD'}"
            )
            self.requestSent = self.SETUP
            threading.Thread(target=self.recvRtspReply).start()

        elif code in (self.PLAY, self.PAUSE, self.TEARDOWN):
            request = (
                f"{code} {self.fileName} RTSP/1.0\n"
                f"CSeq: {self.rtspSeq}\n"
                f"Session: {self.sessionId}"
            )
            self.requestSent = code

        self.rtspSocket.send(request.encode())
        print("[RTSP Request Sent]\n", request)

    # ============================================================
    # RECEIVE RTSP REPLY
    # ============================================================

    def recvRtspReply(self):
        while True:
            try:
                reply = self.rtspSocket.recv(1024)
                if reply:
                    self.parseRtspReply(reply.decode())
                if self.requestSent == self.TEARDOWN:
                    self.rtspSocket.close()
                    break
            except:
                break

    def parseRtspReply(self, data):
        lines = data.split("\n")
        if len(lines) < 3: 
            return

        status = int(lines[0].split(" ")[1])
        seq = int(lines[1].split(" ")[1])
        session = int(lines[2].split(" ")[1])

        if seq != self.rtspSeq:
            return

        if self.sessionId == 0:
            self.sessionId = session

        if self.sessionId != session:
            return

        for line in lines[3:]:
            if line.startswith("Frames"):
                self.totalFrames = int(line.split(" ")[1])

        if status == 200:
            if self.requestSent == self.SETUP:
                self.state = self.READY
                self.openRtpPort()
                print("[RTSP] SETUP OK")

            elif self.requestSent == self.PLAY:
                self.state = self.PLAYING
                print("[RTSP] PLAY OK")

            elif self.requestSent == self.PAUSE:
                self.state = self.READY
                self.isPaused = True
                print("[RTSP] PAUSE OK")

            elif self.requestSent == self.TEARDOWN:
                self.state = self.INIT
                self.playEvent.set()
                print("[RTSP] TEARDOWN OK")

    # ============================================================
    # RTP / LISTEN
    # ============================================================

    def openRtpPort(self):
        self.rtpSockets = []
        for port in (self.rtpPort, self.rtpPort2):
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1 << 20) # 1MB buffer
            try:
                sock.bind(('', port))
                sock.settimeout(0.01)
                self.rtpSockets.append(sock)
                print(f"[RTP] Bound to port {port}")
            except:
                tkinter.messagebox.showwarning("RTP Bind Failed", f"Cannot bind port {port}")

    def listenRtp(self, sock):
        expectedFrame = 1
        frameBuffer = bytearray()

        while True:
            if hasattr(self, "playEvent") and self.playEvent.is_set():
                break

            try:
                data = sock.recv(20480)
            except socket.timeout:
                continue
            except:
                break

            if not data:
                continue

            rtp = RtpPacket()
            rtp.decode(data)
            frameNum = rtp.seqNum()
            self.lastestRenderedFrame = frameNum
            payload = rtp.getPayload()
            marker = rtp.marker()

            if self.isPaused:
                # Buffering logic: if paused, keep buffering until 10% of total frames
                limit = int(self.totalFrames * 0.05)
                if self.cache.size() >= limit:
                    if self.state == self.PLAYING and self.requestSent != self.PAUSE:
                        print(f"[BUFFER] Cache reached 10% ({self.cache.size()}/{limit}), sending PAUSE")
                        self.sendRtspRequest(self.PAUSE)
                    
                    time.sleep(0.02) # Avoid busy wait
                    continue  # stop reading socket
            # frame jump → reset buffer
            if frameNum != expectedFrame:
                frameBuffer = bytearray()
                expectedFrame = frameNum

            if not self.hdMode:
                # SD mode: one full frame
                if self.cache.size() >= self.maxCacheSize:
                    continue  # skip if cache full
                frameBuffer.extend(payload)
                if marker == 1:
                    if self.hd.is_valid_jpeg(frameBuffer):
                        self.latestReceivedFrame = frameNum
                        self.cache.push_frame(frameNum, bytes(frameBuffer))
                    frameBuffer = bytearray()
                    expectedFrame = frameNum + 1

            else:
                # HD mode
                if self.cache.size() >= self.maxCacheSize:
                    continue  # skip if cache full
                frame = self.hd.handle_hd_payload(frameNum, payload, marker)
                if frame:
                    self.latestReceivedFrame = frameNum
                    self.cache.push_frame(frameNum, frame)
                    expectedFrame = frameNum + 1

    # ============================================================
    # PLAYER LOOP
    # ============================================================

    def startPlayerLoop(self):
        self.playerRunning = True
        # Only buffer before the very first frame; do not stall once playback is running
        if not self.bufferWarmed:
            if self.cache.size() < 20 and self.frameNbr < self.totalFrames:
                self.master.after(30, self.startPlayerLoop)
                return
            self.bufferWarmed = True
        if self.state == self.PLAYING and not self.isPaused:
            item = self.cache.pop_frame()
            if item:
                frameNum, frameData = item
                try:
                    photo = self.renderer.build_photo(frameData)
                    self.renderer.render(photo)
                    self.frameNbr = frameNum
                except Exception as e:
                    print("[Render Error]", e)

        # Always update progress bar to show cache filling up
        self.updateProgress(self.frameNbr)

        self.master.after(30, self.startPlayerLoop)

    # ============================================================
    # PROGRESS BAR
    # ============================================================

    def updateProgress(self, frameNum):
        if self.totalFrames <= 0:
            return
        
        width = self.progressCanvas.winfo_width()
        
        # live frame
        live_val = frameNum

        # real cache frame = max frame inside cache, not live+size
        cache_val = min(self.latestReceivedFrame, self.totalFrames)
        # print(
        #     f"[PROGRESS] live={frameNum}, "
        #     f"latestReceived={self.latestReceivedFrame}, "
        #     f"cacheSize={self.cache.size()}, "
        #     f"cacheVal={cache_val}/{self.totalFrames}"
        # )
        live_frac = live_val / self.totalFrames
        cache_frac = cache_val / self.totalFrames

        self.progressCanvas.coords(
            self.liveBarId, 0, 0, width * live_frac, self.progressHeight
        )
        self.progressCanvas.coords(
            self.cacheBarId, 0, 0, width * cache_frac, self.progressHeight
        )
