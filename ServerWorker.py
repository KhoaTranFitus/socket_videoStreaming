from random import randint
from time import time
import sys, traceback, threading, socket, io
from PIL import Image

from VideoStream import VideoStream
from RtpPacket import RtpPacket


class ServerWorker:
	SETUP = 'SETUP'
	PLAY = 'PLAY'
	PAUSE = 'PAUSE'
	TEARDOWN = 'TEARDOWN'
	
	INIT = 0
	READY = 1
	PLAYING = 2
	state = INIT

	OK_200 = 0
	FILE_NOT_FOUND_404 = 1
	CON_ERR_500 = 2

	# For HD, skip downscaling to avoid CPU overhead; keep original quality
	DOWNSCALE_HD = False
	HD_MAX_W = 960
	HD_MAX_H = 540
	HD_QUALITY = 65  # unused when DOWNSCALE_HD is False
	
	clientInfo = {}
	
	def __init__(self, clientInfo):
		self.clientInfo = clientInfo
		
	def run(self):
		threading.Thread(target=self.recvRtspRequest).start()
	
	def recvRtspRequest(self):
		"""Receive RTSP request from the client."""
		connSocket = self.clientInfo['rtspSocket'][0]
		while True:            
			data = connSocket.recv(256)
			if data:
				print("Data received:\n" + data.decode("utf-8"))
				self.processRtspRequest(data.decode("utf-8"))
	
	def processRtspRequest(self, data):
		"""Process RTSP request sent from the client."""
		# Get the request type
		request = data.split('\n')
		line1 = request[0].split(' ')
		requestType = line1[0]
		# Get the media file name
		filename = line1[1]
		
		# Get the RTSP sequence number 
		seq = None
		for line in request:
			if line.startswith("CSeq"):
				seq = line.split(' ')
				break
		cseq = seq[1]
		
		# Detect HD preference
		if requestType == self.SETUP:
			self.isHD = False
			for line in request:
				if "Prefer:" in line and "HD" in line:
					self.isHD = True
		# Process SETUP request
		if requestType == self.SETUP:
			if self.state == self.INIT:
				# Update state
				print("processing SETUP\n")
				
			if self.isHD:
				hd_filename = "movie_HD.Mjpeg"
				print("[HD MODE] Client requests HD stream -> using", hd_filename)
				filename_to_open = hd_filename
			else:
				print("[SD MODE] Normal stream -> using", filename)
				filename_to_open = filename

			try:
				self.clientInfo['videoStream'] = VideoStream(filename_to_open, mode="hd" if self.isHD else "normal")
				self.state = self.READY
			except IOError:
				self.replyRtsp(self.FILE_NOT_FOUND_404, seq[1])
				return

			# Generate a randomized RTSP session ID
			self.clientInfo['session'] = randint(100000, 999999)
				
			# Send RTSP reply
			self.replyRtsp(self.OK_200, seq[1], total_frames=self.clientInfo['videoStream'].totalFrames)
				
			# Get the RTP/UDP port from the last line (primary port). Secondary port is +2.
			self.clientInfo['rtpPort'] = int(request[2].split(' ')[3])
			self.clientInfo['rtpPort2'] = self.clientInfo['rtpPort'] + 2
		
		# Process PLAY request 		
		elif requestType == self.PLAY:
			if self.state == self.READY:
				print("processing PLAY\n")
				self.state = self.PLAYING
				
				self.clientInfo["rtpSocket"] = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
				# Enlarge send buffer to reduce drop
				try:
					self.clientInfo["rtpSocket"].setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 1 << 20) # tăng buffer để tránh drop
				except Exception as exc:
					print("Cannot set SO_SNDBUF:", exc)

				self.replyRtsp(self.OK_200, seq[1])

				self.clientInfo['event'] = threading.Event()

				if self.isHD:
					print(">> Using HD sendRtpHD()")
					self.clientInfo['worker'] = threading.Thread(target=self.sendRtpHD)
				else:
					print(">> Using SD sendRtp()")
					self.clientInfo['worker'] = threading.Thread(target=self.sendRtp)

				self.clientInfo['worker'].start()
		# Process PAUSE request
		elif requestType == self.PAUSE:
			if self.state == self.PLAYING:
				print("processing PAUSE\n")
				self.state = self.READY
				
				self.clientInfo['event'].set() # stop sending RTP packets
			
				self.replyRtsp(self.OK_200, seq[1])
		
		# Process TEARDOWN request
		elif requestType == self.TEARDOWN:
			print("processing TEARDOWN\n")
			self.state = self.INIT
			self.clientInfo['event'].set() # stop sending RTP packets
			
			self.replyRtsp(self.OK_200, seq[1]) # send RTSP reply
			
			# Close the RTP socket
			self.clientInfo['rtpSocket'].close()

	def sendRtp(self):
		"""Send RTP packets over UDP (single packet per frame)."""
		# Target ~60 fps to speed up playback
		frame_interval = 1/30
		next_send = time()
		while True:
			if self.clientInfo['event'].isSet():
				break
			# print ("Sending frame number:", self.clientInfo['videoStream'].frameNbr())
			print("Sending frame number:", self.clientInfo['videoStream'].frameNbr())
			data = self.clientInfo['videoStream'].nextFrame()

			if data:
				frameNumber = self.clientInfo['videoStream'].frameNbr()
				try:
					address = self.clientInfo['rtspSocket'][1][0]
					port = int(self.clientInfo['rtpPort'])
					packet = self.makeRtp(data, frameNumber, marker=1)
					self.clientInfo['rtpSocket'].sendto(packet, (address, port))
				except:
					print("Connection Error")
			else:
				print("[SERVER] End of video reached. Stopping RTP stream.")
				self.clientInfo['event'].set()
				break
			# Pace the stream to target fps but stay responsive to pause
			next_send += frame_interval
			wait_time = max(0, next_send - time())
			if self.clientInfo['event'].wait(wait_time):
				break


	def sendRtpHD(self):
		"""Send RTP packets for HD video (supports fragmentation)."""
		MAX_RTP_PAYLOAD = 1200  # stay under MTU
		frame_interval = 0.05  # send as fast as possible
		next_send = time()

		while True:
			if self.clientInfo['event'].isSet():
				break

			# Send exactly one frame per loop iteration
			frame = self.clientInfo['videoStream'].nextFrame()
			if not frame:
				print("[SERVER] End of HD video reached. Stopping RTP stream.")
				self.clientInfo['event'].set()
				break

			if self.DOWNSCALE_HD:
				frame = self.downscale_frame(frame)

			frameNum = self.clientInfo['videoStream'].frameNbr()
			chunks = [
				frame[i:i + MAX_RTP_PAYLOAD]
				for i in range(0, len(frame), MAX_RTP_PAYLOAD)]
			total_chunks = len(chunks)
			print(f"[SERVER] Sending HD frame: {frameNum} ({total_chunks} chunks)")

			for idx, chunk in enumerate(chunks):
				if self.clientInfo['event'].isSet():
					return
				try:
					marker = 1 if idx == len(chunks) - 1 else 0  # mark the last packet of the frame
					prefixed_chunk = idx.to_bytes(2, "big") + total_chunks.to_bytes(2, "big") + chunk
					packet = self.makeRtp(prefixed_chunk, frameNum, marker=marker)
					address = self.clientInfo['rtspSocket'][1][0]
					if idx % 2 == 0:
						port = self.clientInfo['rtpPort']
					else:
						port = self.clientInfo['rtpPort2']
					self.clientInfo['rtpSocket'].sendto(packet, (address, port))
				except:
					print("Connection Error (HD)")
					break

			if frame_interval > 0:
				next_send += frame_interval
				wait_time = max(0, next_send - time())
				if self.clientInfo['event'].wait(wait_time):
					break
			else:
				if self.clientInfo['event'].wait(0.001):
					break

	def makeRtp(self, payload, frameNbr, marker=0):
		"""RTP-packetize the video data."""
		version = 2
		padding = 0
		extension = 0
		cc = 0
		pt = 26 # MJPEG type
		seqnum = frameNbr
		ssrc = 0 
		
		rtpPacket = RtpPacket()
		
		rtpPacket.encode(version, padding, extension, cc, seqnum, marker, pt, ssrc, payload)
		
		return rtpPacket.getPacket()
			
	def replyRtsp(self, code, seq, total_frames=None):
		"""Send RTSP reply to the client."""
		if code == self.OK_200:
			reply = 'RTSP/1.0 200 OK\nCSeq: ' + seq + '\nSession: ' + str(self.clientInfo['session'])
			if total_frames is not None:
				reply += '\nFrames: ' + str(total_frames)
			connSocket = self.clientInfo['rtspSocket'][0]
			connSocket.send(reply.encode())
		
		# Error messages
		elif code == self.FILE_NOT_FOUND_404:
			print("404 NOT FOUND")
		elif code == self.CON_ERR_500:
			print("500 CONNECTION ERROR")

	def downscale_frame(self, frame_bytes):
		"""Downscale JPEG frame to reduce size before sending."""
		try:
			img = Image.open(io.BytesIO(frame_bytes))
			img.thumbnail((self.HD_MAX_W, self.HD_MAX_H), getattr(Image, "Resampling", Image).LANCZOS)
			buf = io.BytesIO()
			img.save(buf, format="JPEG", quality=self.HD_QUALITY, optimize=True)
			return buf.getvalue()
		except Exception as exc:
			print("Downscale failed, sending original frame:", exc)
			return frame_bytes
