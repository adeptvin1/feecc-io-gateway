import asyncio
import typing as tp
from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException, status
from loguru import logger
from shared.Config import Config

from .camera import Camera, Recording
from .models import CameraList, GenericResponse, RecordList, StartRecordResponse

router = APIRouter()

cameras: tp.Dict[int, Camera] = {}
records: tp.Dict[str, Recording] = {}


def get_camera_by_number(camera_number: int) -> Camera:
    """get a camera by its number"""
    global cameras

    if camera_number in cameras:
        return cameras[camera_number]

    raise HTTPException(status.HTTP_404_NOT_FOUND, f"No such camera: {camera_number}")


def get_record_by_id(record_id: str) -> Recording:
    """get a record by its uuid"""
    global records

    if record_id in records:
        return records[record_id]

    raise HTTPException(status.HTTP_404_NOT_FOUND, f"No such recording: {record_id}")


@router.post("/video/camera/{camera_number}/start", response_model=tp.Union[StartRecordResponse, GenericResponse])  # type: ignore
async def start_recording(
    camera: Camera = Depends(get_camera_by_number),
) -> tp.Union[StartRecordResponse, GenericResponse]:
    """start recording a video using specified camera"""
    global records

    record = Recording(camera.rtsp_stream_link)

    try:
        if not camera.is_up():
            raise BrokenPipeError(f"{camera} is unreachable")

        await record.start()
        records[record.record_id] = record

        message = f"Started recording video for recording {record.record_id}"
        logger.info(message)
        return StartRecordResponse(status=status.HTTP_200_OK, details=message, record_id=record.record_id)

    except Exception as e:
        message = f"Failed to start recording video for recording {record.record_id}: {e}"
        logger.error(message)
        return GenericResponse(status=status.HTTP_500_INTERNAL_SERVER_ERROR, details=message)


@router.post("/video/record/{record_id}/stop", response_model=GenericResponse)
async def end_recording(record: Recording = Depends(get_record_by_id)) -> GenericResponse:
    """finish recording a video"""

    try:
        await record.stop()
        message = f"Stopped recording video for recording {record.record_id}"
        logger.info(message)
        return GenericResponse(status=status.HTTP_200_OK, details=message)

    except Exception as e:
        message = f"Failed to stop recording video for recording {record.record_id}: {e}"
        logger.error(message)
        return GenericResponse(status=status.HTTP_500_INTERNAL_SERVER_ERROR, details=message)


@router.get("/video/cameras", response_model=CameraList)
def get_cameras() -> CameraList:
    """return a list of all connected cameras"""
    global cameras
    cameras_data = [{"number": camera.number, "host": camera.host} for camera in cameras.values()]
    message = f"Collected {len(cameras_data)} cameras"
    logger.info(message)

    return CameraList(status=status.HTTP_200_OK, details=message, cameras=cameras_data)


@router.get("/video/records", response_model=RecordList)
def get_records() -> RecordList:
    """return a list of all tracked records"""
    global records

    ongoing_records = []
    ended_records = []

    for record in records.values():
        record_dict = asdict(record)
        del record_dict["process_ffmpeg"]

        if record.is_ongoing:
            ongoing_records.append(record_dict)
        else:
            ended_records.append(record_dict)

    message = f"Collected {len(ongoing_records)} ongoing and {len(ended_records)} ended records"
    logger.info(message)

    return RecordList(
        status=status.HTTP_200_OK, details=message, ongoing_records=ongoing_records, ended_records=ended_records
    )


async def end_stuck_records(max_duration: int = 60 * 60, interval: int = 60) -> None:
    """If record length exceeds the maximum duration it is considered
    stuck or forgotten and will be stopped. Checked every interval seconds."""
    logger.info(
        f"A daemon was started to monitor stuck records. Update interval is {interval} s., max allowed duration is {max_duration} s."
    )
    global records

    while True:
        await asyncio.sleep(interval)

        for rec_id, rec in filter(lambda r: r[1].is_ongoing, records.items()):
            if len(rec) >= max_duration:
                await records[rec_id].stop()
                logger.warning(f"Recording {rec_id} exceeded {max_duration} s. and was stopped.")


@router.on_event("startup")
@logger.catch
def startup_event() -> None:
    """tasks to do at server startup"""
    global cameras

    for section in Config().camera_config:
        cameras[section["number"]] = Camera(**section)

    logger.info(f"Initialized {len(cameras)} cameras")

    asyncio.create_task(end_stuck_records())