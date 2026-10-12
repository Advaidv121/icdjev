# Security and sensitive data

Do not post credentials, patient notes, or reports containing sensitive evidence
in GitHub issues or pull requests. Rotate exposed credentials immediately.

Report vulnerabilities privately through GitHub's **Report a vulnerability**
option on this repository's Security tab. Do not publicly disclose exploit details
until the issue has been assessed and addressed.

This is an experimental pre-1.0 library. Fixes are targeted at the current main
branch; no long-term support policy or clinical validation is promised.

Hosted lookup sends note text and catalogue context to TypeSafe. Obtain approval
and appropriate vendor agreements before submitting patient information. Keep
keys in a runtime secret store, avoid SDK body/debug logging, and protect any
reports you choose to save. Only load trusted WHO ClaML files.
