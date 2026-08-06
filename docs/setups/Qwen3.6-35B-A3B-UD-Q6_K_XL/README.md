# Notes

- MTP and mmproj are not supported simultaneously
- For MTP, add the following flags and remove the mmproj block
``` json
"--spec-type", "draft-mtp",
"--spec-draft-n-max", 2,*/
```