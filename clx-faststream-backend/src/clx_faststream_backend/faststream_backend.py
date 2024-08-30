import asyncio
import logging
import time
from asyncio import CancelledError, Task
from typing import Callable

from aio_pika import RobustConnection
from attrs import define, field
from faststream import FastStream
from faststream.rabbit import RabbitBroker
from faststream.rabbit.publisher.asyncapi import AsyncAPIPublisher

from clx_common.backends.local_ops_backend import LocalOpsBackend
from clx_common.messaging.base_classes import (
    Payload,
)
from clx_common.messaging.correlation_ids import (CorrelationData,
                                                  active_correlation_ids,
                                                  remove_stale_correlation_ids, )
from clx_common.messaging.routing_keys import (
    DRAWIO_PROCESS_ROUTING_KEY,
    NB_PROCESS_ROUTING_KEY,
    PLANTUML_PROCESS_ROUTING_KEY,
)
from clx_common.operation import Operation
from clx_faststream_backend.faststream_backend_handlers import router

NUM_SEND_RETRIES = 5

logger = logging.getLogger(__name__)


def handle_shutdown_exception(loop, context):
    msg = context.get("exception", context["message"])
    logger.error(f"Caught exception during shutdown: {msg}")


def log_num_active_correlation_ids(cids: dict[str, CorrelationData]):
    num_cids = len(cids)
    logger.info(f"Active correlation IDs: {num_cids}")
    if num_cids < 5:
        for data in cids.values():
            logger.info(f"CID: {data.correlation_id}: {data.format_dependencies()}")


@define
class FastStreamBackend(LocalOpsBackend):
    url: str = "amqp://guest:guest@localhost:5672/"
    broker: RabbitBroker = field(init=False)
    connection: RobustConnection = field(init=False)
    app: FastStream = field(init=False)
    app_task: Task | None = None
    stale_cid_scan_interval: float = 5.0
    stale_cid_max_lifetime: float = 1200.0
    stale_cid_scanner_task: Task | None = None
    start_cid_reporter: bool = True
    cid_reporter_interval: float = 10.0
    cid_reporter_fun: Callable = log_num_active_correlation_ids
    cid_reporter_task: Task | None = None
    shutting_down: bool = False
    services: dict[str, AsyncAPIPublisher] = field(init=False)

    # Maximal number of seconds we wait for all processes to complete
    # Set to a relatively high value, since courses training ML notebooks
    # may run a long time.
    max_wait_for_completion_duration: int = 1200

    def __attrs_post_init__(self):
        self.broker = RabbitBroker(self.url)
        self.broker.include_router(router)
        self.app = FastStream(self.broker)
        self.services = {
            "notebook-processor": self.broker.publisher(NB_PROCESS_ROUTING_KEY),
            "drawio-converter": self.broker.publisher(DRAWIO_PROCESS_ROUTING_KEY),
            "plantuml-converter": self.broker.publisher(PLANTUML_PROCESS_ROUTING_KEY),
        }

    async def __aenter__(self) -> "FastStreamBackend":
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.shutdown()
        return None

    async def start(self):
        self.connection = await self.broker.connect()
        loop = asyncio.get_running_loop()
        self.stale_cid_scanner_task = loop.create_task(
            self.periodically_remove_stale_correlation_ids()
        )
        if self.start_cid_reporter:
            self.cid_reporter_task = loop.create_task(
                self.periodically_report_active_correlation_ids()
            )
        self.app_task = loop.create_task(self.app.run())
        await self.broker.start()

    async def periodically_remove_stale_correlation_ids(self):
        while not self.shutting_down:
            await asyncio.sleep(self.stale_cid_scan_interval)
            await remove_stale_correlation_ids(self.stale_cid_max_lifetime)

    async def periodically_report_active_correlation_ids(self):
        while not self.shutting_down:
            await asyncio.sleep(self.cid_reporter_interval)
            self.cid_reporter_fun(active_correlation_ids)

    async def execute_operation(self, operation: "Operation", payload: Payload) -> None:
        service_name = operation.service_name
        if service_name is None:
            raise ValueError(
                f"{payload.correlation_id}:executing operation without service name"
            )
        await self.send_message(service_name, payload)

    async def send_message(self, service_name: str, payload: Payload):
        service = self.services.get(service_name)
        if service is None:
            raise ValueError(
                f"{payload.correlation_id}:unknown service name:{service_name}"
            )
        correlation_id = payload.correlation_id
        for i in range(NUM_SEND_RETRIES):
            try:
                if i == 0:
                    logger.debug(
                        f"{correlation_id}:FastStreamBackend:publishing "
                        f"{payload.data[:60]}"
                    )
                else:
                    logger.debug(
                        f"{correlation_id}:republishing try {i}:{payload.data[:60]}"
                    )
                await service.publish(payload, correlation_id=correlation_id)
                break
            except CancelledError:
                await asyncio.sleep(1 + i)
                continue
            except Exception as e:
                logger.error(f"{correlation_id}:send_message() failed: {e}")

    async def wait_for_completion(self, max_wait_time: float | None = None) -> bool:
        if max_wait_time is None:
            max_wait_time = self.max_wait_for_completion_duration
        start_time = time.time()
        while True:
            if len(active_correlation_ids) == 0:
                break
            if time.time() - start_time > max_wait_time:
                logger.info("Timed out while waiting for tasks to finish")
                break
            else:
                await asyncio.sleep(1.0)
                logger.debug("Waiting for tasks to finish")
                logger.debug(
                    f"{len(active_correlation_ids)} correlation_id(s) outstanding"
                )
        if len(active_correlation_ids) != 0:
            logger.debug("ERROR: Correlation_ids not empty")
            logger.debug("  Correlation-ids:", active_correlation_ids)
            return False
        return True

    async def shutdown(self):
        try:
            self.shutting_down = True
            await self.wait_for_completion()
            self.app.exit()
            tasks_to_await = [
                task
                for task in [
                    self.app_task,
                    self.stale_cid_scanner_task,
                    self.cid_reporter_task,
                ]
                if task is not None
            ]
            logger.debug("Shutting down pending tasks")
            await asyncio.gather(*tasks_to_await)
            logger.debug("Exited backend")
        except Exception as e:
            logger.error(f"Error while shutting down: {e}")
