"""Build the fixed, tiny local classifier included in the Docker image."""

from pathlib import Path

import numpy as np
import onnx
from onnx import TensorProto, helper, numpy_helper


output = Path(__file__).parents[1] / "app" / "models" / "junk_classifier_v1.onnx"
output.parent.mkdir(parents=True, exist_ok=True)

# Columns: screenshot, document, saved, accidental, ordinary photo.
# Rows: brightness, contrast, sharpness, edges, text, no-camera,
# screen-like aspect, extreme aspect.
weights = np.asarray(
    [
        [0.0, 0.8, 0.0, -0.8, 0.1],
        [0.0, 0.0, 0.0, -2.2, 1.2],
        [0.8, 0.0, 0.2, -0.3, 1.0],
        [0.7, 0.7, 0.8, -1.2, 0.7],
        [0.1, 3.2, 1.4, -0.3, -0.5],
        [1.8, 0.4, 1.8, 0.2, -0.8],
        [2.1, 0.0, 0.0, -0.2, 0.0],
        [0.0, 0.0, 0.4, 1.0, -0.2],
    ],
    dtype=np.float32,
)
bias = np.asarray([-3.0, -3.0, -3.0, 1.0, -0.5], dtype=np.float32)

graph = helper.make_graph(
    [
        helper.make_node("MatMul", ["features", "weights"], ["logits"]),
        helper.make_node("Add", ["logits", "bias"], ["biased"]),
        helper.make_node("Sigmoid", ["biased"], ["scores"]),
    ],
    "photo-review-junk-classifier-v1",
    [helper.make_tensor_value_info("features", TensorProto.FLOAT, [None, 8])],
    [helper.make_tensor_value_info("scores", TensorProto.FLOAT, [None, 5])],
    [
        numpy_helper.from_array(weights, "weights"),
        numpy_helper.from_array(bias, "bias"),
    ],
)
model = helper.make_model(
    graph,
    producer_name="photo-review",
    opset_imports=[helper.make_opsetid("", 17)],
)
model.ir_version = 10
onnx.checker.check_model(model)
onnx.save(model, output)
print(output)

