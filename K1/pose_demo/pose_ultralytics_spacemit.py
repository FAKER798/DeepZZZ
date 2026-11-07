#!/usr/bin/env python3
from __future__ import annotations

import argparse
import time
from pathlib import Path

import cv2
import numpy as np
import onnxruntime as ort

try:
    import spacemit_ort  # noqa: F401  # Registers SpaceMITExecutionProvider with ONNX Runtime.
except Exception as exc:
    print(f"Warning: spacemit_ort import failed: {exc}", flush=True)


PALETTE = [
    (4, 42, 255), (11, 219, 235), (243, 243, 243), (0, 223, 183), (17, 31, 104),
    (255, 111, 221), (255, 68, 79), (204, 237, 0), (0, 243, 68), (189, 0, 255),
    (0, 180, 255), (221, 0, 186), (0, 255, 255), (38, 192, 0), (1, 255, 179),
    (125, 36, 255), (123, 0, 104), (255, 27, 108), (252, 109, 47), (162, 255, 11),
]

POSE_PALETTE = np.array(
    [
        [255, 128, 0], [255, 153, 51], [255, 178, 102], [230, 230, 0], [255, 153, 255],
        [153, 204, 255], [255, 102, 255], [255, 51, 255], [102, 178, 255], [51, 153, 255],
        [255, 153, 153], [255, 102, 102], [255, 51, 51], [153, 255, 153], [102, 255, 102],
        [51, 255, 51], [0, 255, 0], [0, 0, 255], [255, 0, 0], [255, 255, 255],
    ],
    dtype=np.uint8,
)

SKELETON = [
    [16, 14], [14, 12], [17, 15], [15, 13], [12, 13], [6, 12], [7, 13], [6, 7],
    [6, 8], [7, 9], [8, 10], [9, 11], [2, 3], [1, 2], [1, 3], [2, 4], [3, 5],
    [4, 6], [5, 7],
]
LIMB_COLOR = POSE_PALETTE[[9, 9, 9, 9, 7, 7, 7, 0, 0, 0, 0, 0, 16, 16, 16, 16, 16, 16, 16]]
KPT_COLOR = POSE_PALETTE[[16, 16, 16, 16, 16, 0, 0, 0, 0, 0, 0, 9, 9, 9, 9, 9, 9]]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ultralytics-style YOLOv8-pose on SpaceMIT ONNXRuntime.")
    parser.add_argument("--model", default="/home/z/.brdk_models/pose/yolov8n-pose-320.onnx")
    parser.add_argument("--source", default="/dev/video20")
    parser.add_argument("--imgsz", type=int, default=0, help="0 means use static ONNX input size.")
    parser.add_argument("--conf", type=float, default=0.5)
    parser.add_argument("--iou", type=float, default=0.7)
    parser.add_argument("--max-det", type=int, default=20)
    parser.add_argument("--width", type=int, default=1280)
    parser.add_argument("--height", type=int, default=720)
    parser.add_argument("--fps", type=float, default=30.0)
    parser.add_argument("--infer-width", type=int, default=320)
    parser.add_argument("--infer-height", type=int, default=240)
    parser.add_argument("--provider", default="SpaceMITExecutionProvider")
    parser.add_argument("--window", default="YOLO Pose Ultralytics Direct")
    parser.add_argument("--no-display", action="store_true")
    parser.add_argument("--save-video", default="")
    return parser.parse_args()


def color(i: int, bgr: bool = False) -> tuple[int, int, int]:
    c = PALETTE[int(i) % len(PALETTE)]
    return (c[2], c[1], c[0]) if bgr else c


def open_camera(source: str, width: int, height: int, fps: float) -> cv2.VideoCapture:
    src = int(source) if source.isdigit() else source
    cap = cv2.VideoCapture(src, cv2.CAP_V4L2)
    if not cap.isOpened():
        cap.release()
        cap = cv2.VideoCapture(src)
    if width > 0:
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
    if height > 0:
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
    if fps > 0:
        cap.set(cv2.CAP_PROP_FPS, fps)
    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"YUYV"))
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open camera/source: {source}")
    return cap


def make_session(model_path: str, provider: str):
    options = ort.SessionOptions()
    options.intra_op_num_threads = 2
    providers = [provider]
    if provider != "CPUExecutionProvider":
        providers.append("CPUExecutionProvider")
    session = ort.InferenceSession(model_path, sess_options=options, providers=providers)
    return session


def input_hw(session, fallback: int) -> tuple[int, int]:
    shape = session.get_inputs()[0].shape
    h, w = shape[2], shape[3]
    if isinstance(h, int) and isinstance(w, int):
        return h, w
    size = fallback or 320
    return size, size


def letterbox(im: np.ndarray, new_shape: tuple[int, int], stride: int = 32):
    shape = im.shape[:2]
    r = min(new_shape[0] / shape[0], new_shape[1] / shape[1])
    new_unpad = round(shape[1] * r), round(shape[0] * r)
    dw, dh = new_shape[1] - new_unpad[0], new_shape[0] - new_unpad[1]
    dw /= 2
    dh /= 2
    if shape[::-1] != new_unpad:
        im = cv2.resize(im, new_unpad, interpolation=cv2.INTER_LINEAR)
    top, bottom = round(dh - 0.1), round(dh + 0.1)
    left, right = round(dw - 0.1), round(dw + 0.1)
    im = cv2.copyMakeBorder(im, top, bottom, left, right, cv2.BORDER_CONSTANT, value=(114, 114, 114))
    return im, r, (dw, dh)


def preprocess(frame: np.ndarray, input_shape: tuple[int, int]):
    img, ratio, pad = letterbox(frame, input_shape)
    if img.shape[-1] == 3:
        img = img[..., ::-1]
    img = img.transpose((2, 0, 1))
    img = np.ascontiguousarray(img, dtype=np.float32) / 255.0
    return img[None], ratio, pad


def resize_for_inference(frame: np.ndarray, width: int, height: int) -> np.ndarray:
    if width <= 0 or height <= 0:
        return frame
    if frame.shape[1] == width and frame.shape[0] == height:
        return frame
    return cv2.resize(frame, (width, height), interpolation=cv2.INTER_LINEAR)


def xywh2xyxy(x: np.ndarray) -> np.ndarray:
    y = np.empty_like(x)
    y[:, 0] = x[:, 0] - x[:, 2] / 2
    y[:, 1] = x[:, 1] - x[:, 3] / 2
    y[:, 2] = x[:, 0] + x[:, 2] / 2
    y[:, 3] = x[:, 1] + x[:, 3] / 2
    return y


def box_iou(box: np.ndarray, boxes: np.ndarray) -> np.ndarray:
    inter_x1 = np.maximum(box[0], boxes[:, 0])
    inter_y1 = np.maximum(box[1], boxes[:, 1])
    inter_x2 = np.minimum(box[2], boxes[:, 2])
    inter_y2 = np.minimum(box[3], boxes[:, 3])
    inter = np.maximum(0.0, inter_x2 - inter_x1) * np.maximum(0.0, inter_y2 - inter_y1)
    area1 = np.maximum(0.0, box[2] - box[0]) * np.maximum(0.0, box[3] - box[1])
    area2 = np.maximum(0.0, boxes[:, 2] - boxes[:, 0]) * np.maximum(0.0, boxes[:, 3] - boxes[:, 1])
    return inter / np.maximum(area1 + area2 - inter, 1e-7)


def nms(boxes: np.ndarray, scores: np.ndarray, iou_thres: float, max_det: int) -> np.ndarray:
    order = scores.argsort()[::-1]
    keep = []
    while order.size and len(keep) < max_det:
        i = order[0]
        keep.append(i)
        if order.size == 1:
            break
        ious = box_iou(boxes[i], boxes[order[1:]])
        order = order[1:][ious <= iou_thres]
    return np.array(keep, dtype=np.int32)


def clip_boxes(boxes: np.ndarray, shape: tuple[int, int]) -> np.ndarray:
    h, w = shape[:2]
    boxes[:, [0, 2]] = boxes[:, [0, 2]].clip(0, w)
    boxes[:, [1, 3]] = boxes[:, [1, 3]].clip(0, h)
    return boxes


def scale_boxes(boxes: np.ndarray, orig_shape: tuple[int, int], ratio: float, pad: tuple[float, float]) -> np.ndarray:
    boxes[:, [0, 2]] -= pad[0]
    boxes[:, [1, 3]] -= pad[1]
    boxes[:, :4] /= ratio
    return clip_boxes(boxes, orig_shape)


def scale_keypoints(kpts: np.ndarray, orig_shape: tuple[int, int], ratio: float, pad: tuple[float, float]) -> np.ndarray:
    kpts[..., 0] -= pad[0]
    kpts[..., 1] -= pad[1]
    kpts[..., :2] /= ratio
    h, w = orig_shape[:2]
    kpts[..., 0] = kpts[..., 0].clip(0, w)
    kpts[..., 1] = kpts[..., 1].clip(0, h)
    return kpts


def postprocess(output: np.ndarray, orig_shape: tuple[int, int], ratio: float, pad: tuple[float, float],
                conf_thres: float, iou_thres: float, max_det: int) -> list[dict]:
    pred = output[0] if output.ndim == 3 else output
    if pred.shape[0] < pred.shape[1]:
        pred = pred.T
    pred = pred.astype(np.float32, copy=False)
    pred = pred[pred[:, 4] > conf_thres]
    if pred.size == 0:
        return []
    boxes = xywh2xyxy(pred[:, :4])
    scores = pred[:, 4].copy()
    kpts = pred[:, 5:56].reshape(-1, 17, 3).copy()
    order = scores.argsort()[::-1][:30000]
    boxes, scores, kpts = boxes[order], scores[order], kpts[order]
    keep = nms(boxes, scores, iou_thres, max_det)
    boxes = scale_boxes(boxes[keep].copy(), orig_shape, ratio, pad)
    kpts = scale_keypoints(kpts[keep].copy(), orig_shape, ratio, pad)
    scores = scores[keep]
    return [{"box": b, "conf": float(s), "kpts": k} for b, s, k in zip(boxes, scores, kpts)]


def scale_detections(detections: list[dict], src_shape: tuple[int, int], dst_shape: tuple[int, int]) -> list[dict]:
    if not detections:
        return detections
    src_h, src_w = src_shape[:2]
    dst_h, dst_w = dst_shape[:2]
    sx = dst_w / max(src_w, 1)
    sy = dst_h / max(src_h, 1)
    scaled = []
    for det in detections:
        box = det["box"].copy()
        box[[0, 2]] *= sx
        box[[1, 3]] *= sy
        clip_boxes(box.reshape(1, 4), dst_shape)

        kpts = det["kpts"].copy()
        kpts[..., 0] = (kpts[..., 0] * sx).clip(0, dst_w)
        kpts[..., 1] = (kpts[..., 1] * sy).clip(0, dst_h)
        scaled.append({"box": box, "conf": det["conf"], "kpts": kpts})
    return scaled


def line_width(im: np.ndarray) -> int:
    return max(round(sum(im.shape) / 2 * 0.003), 2)


def draw_box_label(im: np.ndarray, box: np.ndarray, label: str, box_color: tuple[int, int, int], lw: int):
    p1 = int(box[0]), int(box[1])
    p2 = int(box[2]), int(box[3])
    cv2.rectangle(im, p1, p2, box_color, lw, lineType=cv2.LINE_AA)
    tf = max(lw - 1, 1)
    sf = lw / 3
    w, h = cv2.getTextSize(label, 0, fontScale=sf, thickness=tf)[0]
    outside = p1[1] >= h + 3
    p2_label = p1[0] + w, p1[1] - h - 3 if outside else p1[1] + h + 3
    cv2.rectangle(im, p1, p2_label, box_color, -1, cv2.LINE_AA)
    txt_y = p1[1] - 2 if outside else p1[1] + h + 2
    cv2.putText(im, label, (p1[0], txt_y), 0, sf, (255, 255, 255), thickness=tf, lineType=cv2.LINE_AA)


def draw_keypoints(im: np.ndarray, kpts: np.ndarray, conf_thres: float, lw: int):
    radius = lw
    h, w = im.shape[:2]
    for i, k in enumerate(kpts):
        x, y, conf = float(k[0]), float(k[1]), float(k[2])
        if x % w != 0 and y % h != 0 and conf >= conf_thres:
            cv2.circle(im, (int(x), int(y)), radius, KPT_COLOR[i].tolist(), -1, lineType=cv2.LINE_AA)
    for i, sk in enumerate(SKELETON):
        k1, k2 = kpts[sk[0] - 1], kpts[sk[1] - 1]
        if k1[2] < conf_thres or k2[2] < conf_thres:
            continue
        pos1 = int(k1[0]), int(k1[1])
        pos2 = int(k2[0]), int(k2[1])
        if pos1[0] % w == 0 or pos1[1] % h == 0 or pos1[0] < 0 or pos1[1] < 0:
            continue
        if pos2[0] % w == 0 or pos2[1] % h == 0 or pos2[0] < 0 or pos2[1] < 0:
            continue
        cv2.line(im, pos1, pos2, LIMB_COLOR[i].tolist(), thickness=int(np.ceil(lw / 2)), lineType=cv2.LINE_AA)


def annotate(frame: np.ndarray, detections: list[dict], inference_ms: float, fps: float) -> np.ndarray:
    im = np.ascontiguousarray(frame.copy())
    lw = line_width(im)
    for det in reversed(detections):
        draw_box_label(im, det["box"], f"person {det['conf']:.2f}", color(0, bgr=True), lw)
        draw_keypoints(im, det["kpts"], conf_thres=0.25, lw=lw)
    status = f"SpaceMIT | {inference_ms:.1f} ms | {fps:.1f} FPS | people {len(detections)}"
    cv2.putText(im, status, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2, lineType=cv2.LINE_AA)
    return im


def make_video_writer(path: str, frame_size: tuple[int, int], fps: float):
    if not path:
        return None
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    writer = cv2.VideoWriter(str(output_path), cv2.VideoWriter_fourcc(*"XVID"), fps or 15.0, frame_size)
    if not writer.isOpened():
        raise RuntimeError(f"Cannot create video file: {output_path}")
    return writer


def main() -> None:
    args = parse_args()
    session = make_session(args.model, args.provider)
    h, w = input_hw(session, args.imgsz)
    if args.imgsz:
        h = w = args.imgsz
    input_name = session.get_inputs()[0].name
    output_names = [x.name for x in session.get_outputs()]
    cap = open_camera(args.source, args.width, args.height, args.fps)
    writer = make_video_writer(args.save_video, (args.width, args.height), args.fps)

    if not args.no_display:
        cv2.namedWindow(args.window, cv2.WINDOW_NORMAL)
        cv2.resizeWindow(args.window, args.width, args.height)
        cv2.moveWindow(args.window, 0, 0)

    print(
        f"YOLO pose started: model={args.model}, provider={args.provider}, "
        f"capture={args.width}x{args.height}@{args.fps:g}, infer_resize={args.infer_width}x{args.infer_height}, "
        f"input={w}x{h}, conf={args.conf}, iou={args.iou}, active_providers={session.get_providers()}",
        flush=True,
    )
    last = time.perf_counter()
    last_log = last
    frame_index = 0
    try:
        while True:
            ok, frame = cap.read()
            if not ok or frame is None:
                print("Camera frame read failed.", flush=True)
                time.sleep(0.05)
                continue
            infer_frame = resize_for_inference(frame, args.infer_width, args.infer_height)
            tensor, ratio, pad = preprocess(infer_frame, (h, w))
            t0 = time.perf_counter()
            outputs = session.run(output_names, {input_name: tensor})
            inference_ms = (time.perf_counter() - t0) * 1000.0
            detections = postprocess(outputs[0], infer_frame.shape, ratio, pad, args.conf, args.iou, args.max_det)
            detections = scale_detections(detections, infer_frame.shape, frame.shape)
            now = time.perf_counter()
            fps = 1.0 / max(now - last, 1e-6)
            last = now
            annotated = annotate(frame, detections, inference_ms, fps)
            frame_index += 1
            if now - last_log >= 2.0:
                print(
                    f"frame={frame_index} infer={inference_ms:.1f}ms fps={fps:.1f} people={len(detections)}",
                    flush=True,
                )
                last_log = now
            if writer is not None:
                writer.write(annotated)
            if not args.no_display:
                cv2.imshow(args.window, annotated)
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    break
    except KeyboardInterrupt:
        pass
    finally:
        cap.release()
        if writer is not None:
            writer.release()
        if not args.no_display:
            cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
