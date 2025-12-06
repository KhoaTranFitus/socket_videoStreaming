# VideoStream.py
class VideoStream:
    def __init__(self, filename, mode="normal"):
        self.filename = filename
        self.file = open(filename, 'rb')
        self.frameNum = 0
        self.mode = mode  # "normal" or "hd"
        self.totalFrames = self._count_frames()

    def frameNbr(self):
        """Return current frame number."""
        return self.frameNum

    # ==========================
    # NORMAL MODE (lab format)
    # ==========================
    def nextFrame_normal(self):
        """Read frame in 5-byte-length format."""
        header = self.file.read(5)
        if len(header) < 5:
            return None
        
        try:
            frameLength = int(header)
        except:
            return None

        data = self.file.read(frameLength)
        return data

    # ==========================
    # HD MODE (JPEG scanning)
    # ==========================
    def nextFrame_hd(self):
        """Read JPEG frame bounded by FF D8 ... FF D9."""
        # 1. find start marker FF D8
        while True:
            b = self.file.read(1)
            if not b:
                return None  # end of file

            if b == b'\xff':
                n = self.file.read(1)
                if n == b'\xd8':
                    frame = b'\xff\xd8'
                    break

        # 2. read until end marker FF D9
        while True:
            byte = self.file.read(1)
            if not byte:
                return None

            frame += byte

            if byte == b'\xd9' and frame[-2] == 0xFF:
                break

        return frame

    # ==========================
    # MODE SELECTOR
    # ==========================
    def nextFrame(self):
        self.frameNum += 1

        if self.mode == "hd":
            return self.nextFrame_hd()
        else:
            return self.nextFrame_normal()

    # ==========================
    # FRAME COUNTING
    # ==========================
    def _count_frames(self):
        """Count total frames in file without disturbing main stream pointer."""
        try:
            with open(self.filename, 'rb') as f:
                if self.mode == "hd":
                    return self._count_hd_frames(f)
                else:
                    return self._count_normal_frames(f)
        except Exception:
            return 0

    def _count_normal_frames(self, fh):
        count = 0
        while True:
            header = fh.read(5)
            if len(header) < 5:
                break
            try:
                frameLength = int(header)
            except:
                break
            data = fh.read(frameLength)
            if len(data) < frameLength:
                break
            count += 1
        return count

    def _count_hd_frames(self, fh):
        count = 0
        while True:
            b = fh.read(1)
            if not b:
                break
            if b == b'\xff':
                n = fh.read(1)
                if n == b'\xd8':
                    # scan to end marker
                    prev = b'\xff'
                    while True:
                        c = fh.read(1)
                        if not c:
                            return count
                        if c == b'\xd9' and prev == b'\xff':
                            count += 1
                            break
                        prev = c
        return count
