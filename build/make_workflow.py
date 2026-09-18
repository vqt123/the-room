"""Emit the API-format workflow for Steal the Moves.

Two variants from one graph:
  workflow_api.json        - the deployment graph: YouTubeFrame (private node) -> Qwen-Image-Edit-2511
  workflow_cloud_test.json - same graph with the private node replaced by a LoadImage, for testing the
                             generation stage on cloud.comfy.org where the private pack does not exist.
Node ids are fixed and documented in README.md; the client sets inputs by these ids.
"""
import json, sys

UNET = "qwen_image_edit_2511_fp8mixed.safetensors"
CLIP = "qwen_2.5_vl_7b_fp8_scaled.safetensors"
VAE = "qwen_image_vae.safetensors"
LORA = "Qwen-Image-Edit-2511-Lightning-4steps-V1.0-bf16.safetensors"

DEFAULT_WHO = "the main person in the centre of the frame"

def prompt_for(who: str = DEFAULT_WHO) -> str:
    return (
        f"In Picture 1, replace only {who} with the person from Picture 2. Everyone else in Picture 1 and everything else "
        "stays exactly as it is: the same body pose, the same move mid-motion, the same camera framing, background, floor "
        "and lighting. The replaced person must have the face, hair, skin tone and build of the person in Picture 2 and "
        "must be caught in the identical pose. Photorealistic, natural, no text."
    )

PROMPT = prompt_for()

def graph(frame_source: str):
    g = {}
    if frame_source == "private":
        g["1"] = {"class_type": "YouTubeFrame", "_meta": {"title": "YouTube Frame (private)"},
                  "inputs": {"url": "https://www.youtube.com/watch?v=Ewqq-3xJFdI", "timestamp": "3:20", "max_height": 720}}
    else:
        g["1"] = {"class_type": "LoadImage", "_meta": {"title": "video frame (test stand-in)"}, "inputs": {"image": "frame.png"}}
    g["2"] = {"class_type": "LoadImage", "_meta": {"title": "profile picture"}, "inputs": {"image": "profile.png"}}
    g["3"] = {"class_type": "UNETLoader", "inputs": {"unet_name": UNET, "weight_dtype": "default"}}
    g["4"] = {"class_type": "CLIPLoader", "inputs": {"clip_name": CLIP, "type": "qwen_image", "device": "default"}}
    g["5"] = {"class_type": "VAELoader", "inputs": {"vae_name": VAE}}
    g["6"] = {"class_type": "ModelSamplingAuraFlow", "inputs": {"model": ["3", 0], "shift": 3.1}}
    g["7"] = {"class_type": "CFGNorm", "inputs": {"model": ["6", 0], "strength": 1.0}}
    g["8"] = {"class_type": "LoraLoaderModelOnly", "inputs": {"model": ["7", 0], "lora_name": LORA, "strength_model": 1.0}}
    g["9"] = {"class_type": "FluxKontextImageScale", "inputs": {"image": ["1", 0]}}
    g["10"] = {"class_type": "TextEncodeQwenImageEditPlus", "_meta": {"title": "positive"},
               "inputs": {"clip": ["4", 0], "prompt": PROMPT, "vae": ["5", 0], "image1": ["9", 0], "image2": ["2", 0]}}
    g["11"] = {"class_type": "TextEncodeQwenImageEditPlus", "_meta": {"title": "negative"},
               "inputs": {"clip": ["4", 0], "prompt": "", "vae": ["5", 0], "image1": ["9", 0], "image2": ["2", 0]}}
    g["12"] = {"class_type": "FluxKontextMultiReferenceLatentMethod",
               "inputs": {"conditioning": ["10", 0], "reference_latents_method": "index_timestep_zero"}}
    g["13"] = {"class_type": "FluxKontextMultiReferenceLatentMethod",
               "inputs": {"conditioning": ["11", 0], "reference_latents_method": "index_timestep_zero"}}
    g["14"] = {"class_type": "VAEEncode", "inputs": {"pixels": ["9", 0], "vae": ["5", 0]}}
    g["15"] = {"class_type": "KSampler", "inputs": {"model": ["8", 0], "positive": ["12", 0], "negative": ["13", 0],
               "latent_image": ["14", 0], "seed": 7, "steps": 4, "cfg": 1.0, "sampler_name": "euler",
               "scheduler": "simple", "denoise": 1.0}}
    g["16"] = {"class_type": "VAEDecode", "inputs": {"samples": ["15", 0], "vae": ["5", 0]}}
    g["17"] = {"class_type": "SaveImage", "inputs": {"images": ["16", 0], "filename_prefix": "ytmove"}}
    return g

if __name__ == "__main__":
    json.dump(graph("private"), open("workflow_api.json", "w"), indent=2)
    json.dump(graph("loadimage"), open("workflow_cloud_test.json", "w"), indent=2)
    # a private-node-only graph to prove the node on any engine without models
    json.dump({"1": graph("private")["1"], "2": {"class_type": "SaveImage", "inputs": {"images": ["1", 0], "filename_prefix": "ytframe_only"}}},
              open("workflow_node_only.json", "w"), indent=2)
    print("wrote workflow_api.json workflow_cloud_test.json workflow_node_only.json")
