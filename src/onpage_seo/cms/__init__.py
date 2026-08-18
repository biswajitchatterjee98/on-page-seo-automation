from onpage_seo.cms.adapters import CmsAdapter, CmsError, NullCmsAdapter, WordPressCmsAdapter, load_cms_adapter
from onpage_seo.cms.fix import apply_suggestion, approve_suggestion, reject_suggestion

__all__ = [
    "CmsAdapter",
    "CmsError",
    "NullCmsAdapter",
    "WordPressCmsAdapter",
    "apply_suggestion",
    "approve_suggestion",
    "load_cms_adapter",
    "reject_suggestion",
]
