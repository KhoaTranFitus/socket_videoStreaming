# renderer.py
from PIL import Image, ImageTk
import io

class Renderer:
    """
    Chỉ chịu trách nhiệm scale ảnh và vẽ lên canvas.
    """

    def __init__(self, canvas, target_w, target_h):
        self.canvas = canvas
        self.canvas_image_id = None
        self.canvas_width = target_w
        self.canvas_height = target_h

    def on_resize(self, event):
        self.canvas_width = event.width
        self.canvas_height = event.height
        if self.canvas_image_id:
            self.canvas.coords(self.canvas_image_id,
                               self.canvas_width // 2,
                               self.canvas_height // 2)

    def build_photo(self, jpeg_bytes: bytes):
        cw, ch = self.canvas_width, self.canvas_height
        img = Image.open(io.BytesIO(jpeg_bytes)).convert("RGB")

        sw, sh = img.size
        scale = min(cw / sw, ch / sh)
        new_w = int(sw * scale)
        new_h = int(sh * scale)
        img = img.resize((new_w, new_h), Image.LANCZOS)

        # Letterbox
        canvas_img = Image.new("RGB", (cw, ch), "black")
        offset = ((cw - new_w) // 2, (ch - new_h) // 2)
        canvas_img.paste(img, offset)

        return ImageTk.PhotoImage(canvas_img)

    def render(self, photo):
        cx = self.canvas_width // 2
        cy = self.canvas_height // 2

        if self.canvas_image_id is None:
            self.canvas_image_id = self.canvas.create_image(cx, cy, image=photo)
        else:
            self.canvas.itemconfig(self.canvas_image_id, image=photo)
            self.canvas.coords(self.canvas_image_id, cx, cy)

        self.canvas.image = photo  # giữ reference
