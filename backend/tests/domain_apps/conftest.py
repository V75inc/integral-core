"""Domain-app test tree — excluded from default pytest via ``domain_app`` marker."""

import pytest

pytestmark = pytest.mark.domain_app
