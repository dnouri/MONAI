# HuggingFace integration

## Installation

Install HuggingFace integration dependencies:

```
  pip install huggingface_hub
  pip install modelcards
```

## Upload

To upload the `spleen_ct_segmentation` model to HuggingFace, run:


```
  python -m monai.bundle upload_zoo_bundle_to_hf --name spleen_ct_segmentation_v0.1.0 --hf_organization myorg --hf_card_data '{"lang": "en"}'
```

## Download

To download the `brats_mri_segmentation` model from HuggingFace, run:

```
  python -m monai.bundle download --name brats_mri_segmentation --source huggingface --repo "dnouri"
```

