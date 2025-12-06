# hd_handler.py
import threading

class HDHandler:
    """
    Xử lý ghép frame HD (nhiều chunk RTP).
    Dùng từ Client.listenRtp().
    """

    def __init__(self):
        # frameParts[frameNum] = { "total": int, "chunks": {idx: bytes} }
        self.frameParts = {}
        self.lock = threading.Lock()

    def reset(self):
        self.frameParts.clear()

    @staticmethod
    def is_valid_jpeg(data: bytes) -> bool:
        return len(data) >= 4 and data[:2] == b'\xff\xd8' and data[-2:] == b'\xff\xd9'

    def handle_hd_payload(self, frameNum, payload, markerBit):
        """Trả về frame hoàn chỉnh hoặc None"""
        if len(payload) < 4:
            return None
        with self.lock:
            idx = int.from_bytes(payload[0:2], "big")
            total = int.from_bytes(payload[2:4], "big")
            chunk = payload[4:]

            entry = self.frameParts.setdefault(frameNum, {"total": total, "chunks": {}})
            entry["chunks"][idx] = chunk

            # ===== ĐỦ CHUNK → GHÉP FRAME =====
            if len(entry["chunks"]) == total:
                ba = bytearray()
                for i in range(total):
                    if i not in entry["chunks"]:
                        print(f"[HD] Missing {i} in frame {frameNum}")
                        self.frameParts.pop(frameNum, None)
                        return None
                    ba.extend(entry["chunks"][i])

                full_frame = bytes(ba)

                if not self.is_valid_jpeg(full_frame):
                    print(f"[HD] Assembled invalid JPEG {frameNum}")
                    self.frameParts.pop(frameNum, None)
                    return None

                self.frameParts.pop(frameNum, None)
                return full_frame

            # ===== Marker nhưng thiếu chunk → drop =====
            if markerBit and len(entry["chunks"]) != total:
                print(f"[HD] Drop frame {frameNum}: missing chunks")
                self.frameParts.pop(frameNum, None)

            return None
