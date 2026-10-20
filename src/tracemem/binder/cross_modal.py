"""OWNS: nothing executable — cross-modal binding is EMERGENT (spec 03 s4).

An ASR claim and an OCR claim in the same (place, epoch) land in the same
episode automatically because claims are modality-uniform and episode_key
ignores modality. This module exists to HOST THE GUARD TEST that proves the
property and to stop anyone re-adding a redundant join step.
"""
EMERGENT = True
