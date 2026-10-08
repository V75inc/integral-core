"""Local PDF module workers must load under the existing strict MIME policy."""

import re
from pathlib import Path

import pytest


@pytest.mark.parametrize("template", ["nginx.conf", "nginx.docker.conf"])
def test_module_assets_have_javascript_mime_and_no_spa_fallback(template):
    config = (Path(__file__).resolve().parents[2] / "frontend" / template).read_text()
    module_location = re.search(r"location ~ \\\.mjs\$\s*\{(.*?)\n    \}", config, re.S)
    assert module_location is not None
    block = module_location.group(1)
    assert "types { application/javascript mjs; }" in block
    assert "try_files $uri =404;" in block
    assert "index.html" not in block
    # Adding location-level headers would suppress inherited security headers.
    assert "add_header" not in block
    assert 'add_header X-Content-Type-Options "nosniff" always;' in config
    assert "worker-src 'self' blob:" in config
