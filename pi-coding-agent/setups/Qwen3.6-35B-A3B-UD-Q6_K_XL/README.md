# Notes

- MTP works and mmproj are not supported simultaneously
- For MTP add following and remove mmproj block
``` json
"--spec-type", "draft-mtp",
"--spec-draft-n-max", 2,*/
```