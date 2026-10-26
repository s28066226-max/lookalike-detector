"""Look-Alike Website Detector: tells whether a URL is a genuine Indian bank/government site or a look-alike."""

import warnings

# Harmless library warnings that would clutter the demo output:
# urllib3 (via tldextract) dislikes macOS's LibreSSL, google-auth dislikes Python 3.9.
warnings.filterwarnings("ignore", message="urllib3 v2 only supports OpenSSL")
warnings.filterwarnings("ignore", category=FutureWarning, module="google")
