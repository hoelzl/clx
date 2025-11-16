from pathlib import Path

from attrs import define

from clx.core.course_file import CourseFile
from clx.core.utils.text_utils import sanitize_file_name
from clx.infrastructure.operation import Operation


@define
class DrawIoFile(CourseFile):
    async def get_processing_operation(self, target_dir: Path) -> Operation:
        from clx.core.operations.convert_drawio_file import ConvertDrawIoFileOperation

        return ConvertDrawIoFileOperation(
            input_file=self,
            output_file=self.img_path,
        )

    @property
    def img_path(self) -> Path:
        sanitized_name = sanitize_file_name(self.path.stem)
        return (self.path.parents[1] / "img" / sanitized_name).with_suffix(".png")

    @property
    def source_outputs(self) -> frozenset[Path]:
        return frozenset({self.img_path})
