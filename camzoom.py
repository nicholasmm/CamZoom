"""
CamZoom - zoom digital dinâmico para webcam, publicado como câmera virtual.

Lê a webcam real (ex.: 2560x1440), recorta/amplia a região escolhida e envia
o resultado para a "OBS Virtual Camera". Nas reuniões (Teams, Meet, Zoom...),
selecione "OBS Virtual Camera" como câmera.
"""

import json
import os
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox

import cv2
import numpy as np
from PIL import Image, ImageTk

try:
    import pyvirtualcam
except ImportError:  # pragma: no cover
    pyvirtualcam = None

try:
    from pygrabber.dshow_graph import FilterGraph
except ImportError:  # pragma: no cover
    FilterGraph = None


APP_DIR = os.path.dirname(os.path.abspath(sys.argv[0]))
# Arquivos empacotados no .exe ficam em sys._MEIPASS
RES_DIR = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
SETTINGS_FILE = os.path.join(APP_DIR, "camzoom_settings.json")

OUTPUT_RESOLUTIONS = {
    "1280x720 (HD)": (1280, 720),
    "1920x1080 (Full HD)": (1920, 1080),
}
INPUT_RESOLUTIONS = {
    "2560x1440 (1440p)": (2560, 1440),
    "1920x1080 (1080p)": (1920, 1080),
    "1280x720 (720p)": (1280, 720),
}
VIRTUAL_CAM_NAMES = ("obs virtual camera", "unity video capture")

DEFAULTS = {
    "camera_name": "",
    "input_res": "2560x1440 (1440p)",
    "output_res": "1280x720 (HD)",
    "fps": 30,
    "zoom": 1.6,
    "pan_x": 0.0,
    "pan_y": 0.0,
    "mirror": False,
    "follow_face": False,
    "smoothness": 0.85,
}

def _load_face_cascade():
    """Carrega o detector pela memória: o OpenCV não abre caminhos com acentos
    (ex.: "Área de Trabalho") no Windows."""
    path = os.path.join(cv2.data.haarcascades, "haarcascade_frontalface_default.xml")
    try:
        with open(path, "r", encoding="utf-8") as f:
            xml = f.read()
        fs = cv2.FileStorage(xml, cv2.FILE_STORAGE_READ | cv2.FILE_STORAGE_MEMORY)
        cascade = cv2.CascadeClassifier()
        if cascade.read(fs.getFirstTopLevelNode()):
            return cascade
    except Exception:
        pass
    return None


FACE_CASCADE = _load_face_cascade()


def list_cameras():
    """Retorna [(indice, nome)] das câmeras DirectShow, sem as virtuais."""
    if FilterGraph is not None:
        try:
            names = FilterGraph().get_input_devices()
            return [
                (i, n) for i, n in enumerate(names)
                if n.lower() not in VIRTUAL_CAM_NAMES
            ]
        except Exception:
            pass
    return [(i, f"Câmera {i}") for i in range(3)]


def load_settings():
    s = dict(DEFAULTS)
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            s.update(json.load(f))
    except (OSError, ValueError):
        pass
    return s


def save_settings(s):
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(s, f, indent=2, ensure_ascii=False)
    except OSError:
        pass


class ZoomEngine:
    """Captura, recorta e publica os frames numa thread separada."""

    def __init__(self):
        self.lock = threading.Lock()
        self.running = False
        self.thread = None
        self.preview = None          # último frame de saída (BGR)
        self.status = "Parado"
        self.actual_fps = 0.0
        self.input_size = (0, 0)

        # Parâmetros ajustáveis em tempo real
        self.zoom = 1.0
        self.pan_x = 0.0             # -1 (esquerda) .. 1 (direita)
        self.pan_y = 0.0             # -1 (cima) .. 1 (baixo)
        self.mirror = False
        self.follow_face = False
        self.smoothness = 0.85       # 0 = instantâneo, 0.97 = bem suave

        # Estado suavizado (o que é efetivamente aplicado)
        self._cur_zoom = 1.0
        self._cur_cx = None
        self._cur_cy = None
        self._face_center = None
        self._frame_count = 0

    # ---------- ciclo de vida ----------
    def start(self, cam_index, in_size, out_size, fps):
        self.stop()
        self.running = True
        self.thread = threading.Thread(
            target=self._run, args=(cam_index, in_size, out_size, fps), daemon=True
        )
        self.thread.start()

    def stop(self):
        self.running = False
        if self.thread is not None:
            self.thread.join(timeout=3)
            self.thread = None

    # ---------- processamento ----------
    def _open_camera(self, index, in_size, fps):
        cap = cv2.VideoCapture(index, cv2.CAP_DSHOW)
        if not cap.isOpened():
            return None
        # MJPG é necessário para 1440p a 30 fps na maioria das webcams USB
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, in_size[0])
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, in_size[1])
        cap.set(cv2.CAP_PROP_FPS, fps)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        return cap

    def _detect_face(self, frame):
        if FACE_CASCADE is None:
            return None
        h, w = frame.shape[:2]
        scale = 480.0 / w
        small = cv2.resize(frame, (480, int(h * scale)))
        gray = cv2.equalizeHist(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY))
        faces = FACE_CASCADE.detectMultiScale(
            gray, scaleFactor=1.15, minNeighbors=5, minSize=(40, 40)
        )
        if len(faces) == 0:
            return None
        x, y, fw, fh = max(faces, key=lambda f: f[2] * f[3])
        # centro um pouco abaixo do rosto, para enquadrar rosto + ombros
        return ((x + fw / 2) / scale, (y + fh * 0.9) / scale)

    def _compute_crop(self, frame, out_size):
        h, w = frame.shape[:2]
        aspect = out_size[0] / out_size[1]
        a = self.smoothness

        self._cur_zoom = a * self._cur_zoom + (1 - a) * self.zoom
        zoom = max(1.0, self._cur_zoom)

        # maior recorte possível com o aspecto de saída, dividido pelo zoom
        base_w, base_h = (w, w / aspect) if w / h <= aspect else (h * aspect, h)
        cw, ch = base_w / zoom, base_h / zoom

        if self.follow_face and self._face_center is not None:
            tx, ty = self._face_center
        else:
            tx = w / 2 + self.pan_x * (w - cw) / 2
            ty = h / 2 + self.pan_y * (h - ch) / 2

        if self._cur_cx is None:
            self._cur_cx, self._cur_cy = tx, ty
        self._cur_cx = a * self._cur_cx + (1 - a) * tx
        self._cur_cy = a * self._cur_cy + (1 - a) * ty

        cx = min(max(self._cur_cx, cw / 2), w - cw / 2)
        cy = min(max(self._cur_cy, ch / 2), h - ch / 2)
        x0, y0 = int(round(cx - cw / 2)), int(round(cy - ch / 2))
        return x0, y0, int(round(cw)), int(round(ch))

    def process(self, frame, out_size):
        self._frame_count += 1
        if self.follow_face and self._frame_count % 5 == 0:
            c = self._detect_face(frame)
            if c is not None:
                self._face_center = c
        x, y, cw, ch = self._compute_crop(frame, out_size)
        crop = frame[y:y + ch, x:x + cw]
        interp = cv2.INTER_AREA if cw > out_size[0] else cv2.INTER_LINEAR
        out = cv2.resize(crop, out_size, interpolation=interp)
        if self.mirror:
            out = cv2.flip(out, 1)
        return out

    def _run(self, cam_index, in_size, out_size, fps):
        self.status = "Abrindo câmera..."
        cap = self._open_camera(cam_index, in_size, fps)
        if cap is None:
            self.status = "Erro: não foi possível abrir a câmera"
            self.running = False
            return
        ok, frame = cap.read()
        if not ok:
            self.status = "Erro: a câmera não retornou imagem (está em uso?)"
            cap.release()
            self.running = False
            return
        self.input_size = (frame.shape[1], frame.shape[0])

        vcam = None
        if pyvirtualcam is not None:
            try:
                vcam = pyvirtualcam.Camera(
                    width=out_size[0], height=out_size[1], fps=fps,
                    fmt=pyvirtualcam.PixelFormat.BGR, backend="obs",
                )
            except Exception as e:
                self.status = f"Sem câmera virtual ({e}). Apenas pré-visualização."
        if vcam is not None:
            self.status = f"Transmitindo para '{vcam.device}'"

        t_last, frames = time.time(), 0
        try:
            while self.running:
                ok, frame = cap.read()
                if not ok:
                    time.sleep(0.01)
                    continue
                out = self.process(frame, out_size)
                if vcam is not None:
                    vcam.send(out)
                with self.lock:
                    self.preview = out
                frames += 1
                now = time.time()
                if now - t_last >= 1.0:
                    self.actual_fps = frames / (now - t_last)
                    t_last, frames = now, 0
        finally:
            cap.release()
            if vcam is not None:
                vcam.close()
            self.status = "Parado"
            with self.lock:
                self.preview = None


class App:
    def __init__(self, root):
        self.root = root
        self.engine = ZoomEngine()
        self.settings = load_settings()
        self.cameras = list_cameras()
        self._photo = None

        root.title("CamZoom - zoom para webcam")
        try:
            root.iconbitmap(os.path.join(RES_DIR, "camzoom.ico"))
        except tk.TclError:
            pass
        root.minsize(760, 560)
        root.protocol("WM_DELETE_WINDOW", self.on_close)

        self._build_ui()
        self._apply_settings_to_engine()
        self._bind_keys()
        self.root.after(33, self._update_preview)

        if self.cameras:
            self.toggle()  # inicia automaticamente

    # ---------- UI ----------
    def _build_ui(self):
        s = self.settings
        main = ttk.Frame(self.root, padding=10)
        main.pack(fill="both", expand=True)

        # Linha de configuração da câmera
        top = ttk.Frame(main)
        top.pack(fill="x")
        ttk.Label(top, text="Câmera:").pack(side="left")
        cam_names = [n for _, n in self.cameras] or ["(nenhuma câmera)"]
        self.cam_var = tk.StringVar(
            value=s["camera_name"] if s["camera_name"] in cam_names else cam_names[0]
        )
        ttk.Combobox(top, textvariable=self.cam_var, values=cam_names,
                     state="readonly", width=24).pack(side="left", padx=(4, 12))

        ttk.Label(top, text="Entrada:").pack(side="left")
        self.in_var = tk.StringVar(value=s["input_res"])
        ttk.Combobox(top, textvariable=self.in_var, values=list(INPUT_RESOLUTIONS),
                     state="readonly", width=17).pack(side="left", padx=(4, 12))

        ttk.Label(top, text="Saída:").pack(side="left")
        self.out_var = tk.StringVar(value=s["output_res"])
        ttk.Combobox(top, textvariable=self.out_var, values=list(OUTPUT_RESOLUTIONS),
                     state="readonly", width=18).pack(side="left", padx=(4, 12))

        self.btn = ttk.Button(top, text="Iniciar", command=self.toggle, width=10)
        self.btn.pack(side="right")

        # Pré-visualização
        # O painel tem tamanho próprio e ignora o tamanho da imagem
        # (pack_propagate False); sem isso a janela cresce a cada quadro.
        self.preview_box = tk.Frame(main, bg="#111", width=720, height=405)
        self.preview_box.pack(fill="both", expand=True, pady=10)
        self.preview_box.pack_propagate(False)
        self.preview_lbl = tk.Label(self.preview_box, bg="#111", fg="#888",
                                    text="Pré-visualização desligada")
        self.preview_lbl.pack(fill="both", expand=True)

        # Controles de enquadramento
        ctl = ttk.Frame(main)
        ctl.pack(fill="x")
        ctl.columnconfigure(1, weight=1)

        self.zoom_var = tk.DoubleVar(value=s["zoom"])
        self.panx_var = tk.DoubleVar(value=s["pan_x"])
        self.pany_var = tk.DoubleVar(value=s["pan_y"])
        self.smooth_var = tk.DoubleVar(value=s["smoothness"])
        rows = [
            ("Zoom", self.zoom_var, 1.0, 4.0),
            ("Horizontal", self.panx_var, -1.0, 1.0),
            ("Vertical", self.pany_var, -1.0, 1.0),
            ("Suavidade", self.smooth_var, 0.0, 0.97),
        ]
        self.value_lbls = {}
        for r, (label, var, lo, hi) in enumerate(rows):
            ttk.Label(ctl, text=label, width=11).grid(row=r, column=0, sticky="w")
            ttk.Scale(ctl, variable=var, from_=lo, to=hi,
                      command=lambda _v: self._apply_settings_to_engine()
                      ).grid(row=r, column=1, sticky="ew", padx=6, pady=2)
            vl = ttk.Label(ctl, width=6)
            vl.grid(row=r, column=2, sticky="e")
            self.value_lbls[label] = (vl, var)

        opts = ttk.Frame(main)
        opts.pack(fill="x", pady=(8, 0))
        self.mirror_var = tk.BooleanVar(value=s["mirror"])
        self.face_var = tk.BooleanVar(value=s["follow_face"])
        ttk.Checkbutton(opts, text="Espelhar", variable=self.mirror_var,
                        command=self._apply_settings_to_engine).pack(side="left")
        ttk.Checkbutton(opts, text="Seguir rosto automaticamente", variable=self.face_var,
                        command=self._apply_settings_to_engine).pack(side="left", padx=12)
        ttk.Button(opts, text="Centralizar", command=self.reset_pan).pack(side="left")
        ttk.Button(opts, text="Sem zoom", command=lambda: self.set_zoom(1.0)
                   ).pack(side="left", padx=6)

        self.status_var = tk.StringVar()
        ttk.Label(main, textvariable=self.status_var, foreground="#555"
                  ).pack(fill="x", pady=(8, 0))
        ttk.Label(main, foreground="#777", text=(
            "Atalhos: + / − zoom · setas movem o enquadramento · F seguir rosto · "
            "C centralizar.  Na reunião, escolha a câmera \"OBS Virtual Camera\"."
        )).pack(fill="x")

    def _bind_keys(self):
        r = self.root
        r.bind("<plus>", lambda e: self.set_zoom(self.zoom_var.get() + 0.1))
        r.bind("<KP_Add>", lambda e: self.set_zoom(self.zoom_var.get() + 0.1))
        r.bind("<equal>", lambda e: self.set_zoom(self.zoom_var.get() + 0.1))
        r.bind("<minus>", lambda e: self.set_zoom(self.zoom_var.get() - 0.1))
        r.bind("<KP_Subtract>", lambda e: self.set_zoom(self.zoom_var.get() - 0.1))
        r.bind("<Left>", lambda e: self.nudge(self.panx_var, -0.05))
        r.bind("<Right>", lambda e: self.nudge(self.panx_var, 0.05))
        r.bind("<Up>", lambda e: self.nudge(self.pany_var, -0.05))
        r.bind("<Down>", lambda e: self.nudge(self.pany_var, 0.05))
        r.bind("<f>", lambda e: (self.face_var.set(not self.face_var.get()),
                                 self._apply_settings_to_engine()))
        r.bind("<c>", lambda e: self.reset_pan())
        # Roda do mouse sobre a pré-visualização = zoom
        self.preview_lbl.bind("<MouseWheel>", lambda e: self.set_zoom(
            self.zoom_var.get() + (0.1 if e.delta > 0 else -0.1)))

    # ---------- ações ----------
    def set_zoom(self, z):
        self.zoom_var.set(round(min(4.0, max(1.0, z)), 2))
        self._apply_settings_to_engine()

    def nudge(self, var, d):
        var.set(min(1.0, max(-1.0, var.get() + d)))
        self._apply_settings_to_engine()

    def reset_pan(self):
        self.panx_var.set(0.0)
        self.pany_var.set(0.0)
        self._apply_settings_to_engine()

    def _apply_settings_to_engine(self):
        e = self.engine
        e.zoom = self.zoom_var.get()
        e.pan_x = self.panx_var.get()
        e.pan_y = self.pany_var.get()
        e.smoothness = self.smooth_var.get()
        e.mirror = self.mirror_var.get()
        if self.face_var.get() and not e.follow_face:
            e._face_center = None
        e.follow_face = self.face_var.get()
        fmt = {"Zoom": "{:.2f}x", "Suavidade": "{:.2f}"}
        for label, (lbl, var) in self.value_lbls.items():
            lbl.config(text=fmt.get(label, "{:+.2f}").format(var.get()))

    def toggle(self):
        if self.engine.running:
            self.engine.stop()
            self.btn.config(text="Iniciar")
            return
        name = self.cam_var.get()
        idx = next((i for i, n in self.cameras if n == name), None)
        if idx is None:
            messagebox.showerror("CamZoom", "Nenhuma câmera selecionada.")
            return
        fps = int(self.settings.get("fps", 30))
        self.engine.start(idx, INPUT_RESOLUTIONS[self.in_var.get()],
                          OUTPUT_RESOLUTIONS[self.out_var.get()], fps)
        self.btn.config(text="Parar")

    def _update_preview(self):
        e = self.engine
        with e.lock:
            frame = e.preview
        if frame is not None:
            lw = max(self.preview_box.winfo_width(), 100)
            lh = max(self.preview_box.winfo_height(), 100)
            fh, fw = frame.shape[:2]
            scale = min(lw / fw, lh / fh)
            small = cv2.resize(frame, (max(1, int(fw * scale)), max(1, int(fh * scale))),
                               interpolation=cv2.INTER_AREA)
            img = Image.fromarray(cv2.cvtColor(small, cv2.COLOR_BGR2RGB))
            self._photo = ImageTk.PhotoImage(img)
            self.preview_lbl.config(image=self._photo, text="")
        elif self._photo is not None:
            self._photo = None
            self.preview_lbl.config(image="", text="Pré-visualização desligada")

        if not e.running and self.btn.cget("text") == "Parar":
            self.btn.config(text="Iniciar")
        info = e.status
        if e.running and e.input_size[0]:
            info += f"  ·  entrada {e.input_size[0]}x{e.input_size[1]}  ·  {e.actual_fps:.0f} fps"
        self.status_var.set(info)
        self.root.after(33, self._update_preview)

    def on_close(self):
        self.settings.update({
            "camera_name": self.cam_var.get(),
            "input_res": self.in_var.get(),
            "output_res": self.out_var.get(),
            "zoom": self.zoom_var.get(),
            "pan_x": self.panx_var.get(),
            "pan_y": self.pany_var.get(),
            "mirror": self.mirror_var.get(),
            "follow_face": self.face_var.get(),
            "smoothness": self.smooth_var.get(),
        })
        save_settings(self.settings)
        self.engine.stop()
        self.root.destroy()


def main():
    try:
        from ctypes import windll
        windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
