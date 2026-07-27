import shutil

import pytest


@pytest.fixture(autouse=True)
def isolate_sorting_api_archive(request):
    """Clean only the temporary archive owned by the sorting API test class."""
    instance = getattr(request, "instance", None)
    if instance is None or instance.__class__.__name__ != "SortingApiRegressionTests":
        yield
        return
    module = getattr(instance.__class__, "module", None)
    if module is not None and module.config.photos_root.is_dir():
        for child in module.config.photos_root.iterdir():
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()
    yield
