from typing import Any, Dict, Optional

from osw_sanitizer import OSWSanitization, SanitizationConfig


class SanitizationProcessor:
    """
    Compatibility adapter for the sanitization service.

    The OSW sanitization implementation lives in the osw-sanitizer package.
    Keeping this adapter avoids touching existing service orchestration code
    that imports SanitizationProcessor directly.
    """

    @classmethod
    def sanitize_dataset(
        cls,
        input_zip_path: str,
        output_dir: str,
        config: Optional[SanitizationConfig] = None,
    ) -> Dict[str, Any]:
        return OSWSanitization.sanitize_dataset(
            input_zip_path=input_zip_path,
            output_dir=output_dir,
            config=config,
        )
