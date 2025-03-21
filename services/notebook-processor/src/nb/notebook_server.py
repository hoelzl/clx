import asyncio
import logging
import os
import traceback

from aio_pika import RobustConnection
from aio_pika.abc import AbstractRobustChannel
from aiormq.abc import AbstractChannel
from faststream import FastStream
from faststream.rabbit import RabbitBroker

from clx_common.messaging.base_classes import ProcessingError
from clx_common.messaging.notebook_classes import (
    NotebookPayload,
    NotebookResult,
    NotebookResultOrError,
    notebook_metadata_tags,
)
from clx_common.messaging.routing_keys import (
    NB_PROCESS_ROUTING_KEY,
    NB_RESULT_ROUTING_KEY,
)
from .notebook_processor import NotebookProcessor
from .output_spec import create_output_spec

# Configuration
RABBITMQ_URL = os.environ.get("RABBITMQ_URL", "amqp://guest:guest@localhost/")
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
LOG_CELL_PROCESSING = os.environ.get("LOG_CELL_PROCESSING", "False") == "True"

# Logging setup
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL),
    format="%(asctime)s - notebook-processor - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Set up RabbitMQ broker and app
broker = RabbitBroker(RABBITMQ_URL)
app = FastStream(broker)


@broker.subscriber(NB_PROCESS_ROUTING_KEY)
@broker.publisher(NB_RESULT_ROUTING_KEY)
async def process_notebook(payload: NotebookPayload) -> NotebookResultOrError:
    cid = payload.correlation_id
    try:
        logger.debug(f"{cid}:Processing notebook payload for '{payload.data}'")
        output_spec = create_output_spec(
            kind=payload.kind,
            prog_lang=payload.prog_lang,
            language=payload.language,
            format=payload.format,
        )
        logger.debug(f"{cid}:Output spec created")
        processor = NotebookProcessor(output_spec)
        processed_notebook = await processor.process_notebook(payload)
        logger.debug(f"{cid}:Processed notebook: {processed_notebook[:60]}")
        return NotebookResult(
            result=processed_notebook,
            correlation_id=cid,
            output_file=payload.output_file,
            input_file=payload.input_file,
            content_hash=payload.content_hash(),
            output_metadata_tags=notebook_metadata_tags(
                kind=payload.kind,
                prog_lang=payload.prog_lang,
                language=payload.language,
                output_format=payload.format,
            ),
        )
    except Exception as e:
        file_name = payload.input_file_name
        logger.error(
            f"{cid}:Notebook Server: Error while processing notebook {file_name}: {e}"
        )
        logger.debug(f"{cid}:Error traceback for {file_name}:", exc_info=e)
        return ProcessingError(
            error=str(e),
            correlation_id=cid,
            input_file=payload.input_file,
            input_file_name=payload.input_file_name,
            output_file=payload.output_file,
            traceback=traceback.format_exc(),
        )


@app.after_startup
async def configure_channels():
    logger.info("Configuring channels")
    connection: RobustConnection = await app.broker.connect()
    robust_channel: AbstractRobustChannel = await connection.channel()
    channel: AbstractChannel = await robust_channel.get_underlay_channel()
    logger.debug("Obtained channel")
    await channel.basic_qos(prefetch_count=1)


if __name__ == "__main__":
    asyncio.run(app.run())
