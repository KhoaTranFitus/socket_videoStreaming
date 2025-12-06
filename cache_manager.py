# cache_manager.py
import queue

class CacheManager:
    """
    Quản lý cache frame (queue).
    Có thể thay đổi thuật toán drop rất dễ dàng.
    """

    def __init__(self, max_size=200):
        self.queue = queue.Queue(maxsize=max_size)

    def push_frame(self, frameNum, frameData):
        """Thêm frame vào cache. Drop nếu đầy."""
        if self.queue.full():
            try:
                self.queue.get_nowait()
            except queue.Full:
                print(f"[CACHE] Full → Drop frame {frameNum}")
        try:
            self.queue.put_nowait((frameNum, frameData))
        except queue.Full:
            pass
    def pop_frame(self):
        """Lấy 1 frame để render. Nếu không có thì trả None."""
        if self.queue.empty():
            return None
        return self.queue.get()

    def size(self):
        return self.queue.qsize()

    def clear(self):
        while not self.queue.empty():
            self.queue.get()
    
