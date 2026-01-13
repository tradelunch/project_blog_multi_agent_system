# agents/image_processing_agent.py
"""
ImageProcessingAgent - Image resizing and optimization for social sharing.

Resizes images to OG-friendly dimensions (1200x630) with letterboxing
(transparent padding) to prevent distortion.
"""

from typing import Dict, Any, Optional, Tuple
from pathlib import Path
from .base import BaseAgent


class ImageProcessingAgent(BaseAgent):
    """
    Image processing agent for social media optimization.

    Resizes images to Open Graph dimensions with transparent padding
    to maintain aspect ratio without distortion.
    """

    # Default OG image size (Facebook/LinkedIn standard)
    DEFAULT_OG_SIZE = (1200, 630)

    def __init__(self):
        super().__init__(
            name="ImageProcessingAgent",
            description="Resize images for OG/social sharing",
        )

    async def execute(self, task: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute image processing task.

        Actions:
            - resize_for_og: Resize image to OG dimensions with letterboxing
        """
        action = task.get("action")
        data = task.get("data", {})

        try:
            if action == "resize_for_og":
                local_path = data.get("local_path")
                output_path = data.get("output_path")
                target_size = data.get("target_size", self.DEFAULT_OG_SIZE)

                if not local_path:
                    return {"success": False, "error": "local_path is required"}

                result_path = self.resize_for_og(
                    image_path=local_path,
                    output_path=output_path,
                    target_size=tuple(target_size) if isinstance(target_size, list) else target_size,
                )

                return {
                    "success": True,
                    "data": {
                        "output_path": result_path,
                        "target_size": target_size,
                    },
                    "agent": self.name,
                }

            else:
                return {"success": False, "error": f"Unknown action: {action}"}

        except Exception as e:
            self._log(f"Image processing failed: {e}", "error")
            return {"success": False, "error": str(e), "agent": self.name}

    def resize_for_og(
        self,
        image_path: str,
        output_path: Optional[str] = None,
        target_size: Tuple[int, int] = (1200, 630),
    ) -> str:
        """
        Resize image to OG dimensions with letterboxing (transparent padding).

        The image is scaled to fit within target_size while maintaining
        aspect ratio. Empty space is filled with transparent pixels.

        Args:
            image_path: Path to source image
            output_path: Path for output (default: overwrite source)
            target_size: Target dimensions (width, height)

        Returns:
            Path to resized image
        """
        from PIL import Image

        # Open and convert to RGBA for transparency support
        img = Image.open(image_path).convert("RGBA")
        original_size = img.size
        
        # Scale image to fit within target bounds (maintain aspect ratio)
        img.thumbnail(target_size, Image.LANCZOS)
        scaled_size = img.size

        # Create transparent canvas
        canvas = Image.new("RGBA", target_size, (0, 0, 0, 0))

        # Center the image on canvas
        x = (target_size[0] - img.width) // 2
        y = (target_size[1] - img.height) // 2
        canvas.paste(img, (x, y))

        # Save result
        out_path = output_path or image_path
        canvas.save(out_path, "PNG")

        self._log(
            f"Resized {Path(image_path).name}: {original_size} → {scaled_size} "
            f"(canvas: {target_size})"
        )

        return out_path

    def get_image_dimensions(self, image_path: str) -> Tuple[int, int]:
        """Get image dimensions without loading full image."""
        from PIL import Image

        with Image.open(image_path) as img:
            return img.size
