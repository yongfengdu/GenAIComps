# Copyright (C) 2024 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

import logging
import os
import re

from fastapi import HTTPException

from comps import CustomLogger, LVMVideoDoc, OpeaComponentRegistry, SearchedMultimodalDoc, ServiceType
from comps.cores.common.component import OpeaComponent

logger = CustomLogger("video_reranking")
logflag = os.getenv("LOGFLAG", False)

chunk_duration = os.getenv("CHUNK_DURATION", "10") or "10"
chunk_duration = float(chunk_duration) if chunk_duration.isdigit() else 10.0

file_server_endpoint = os.getenv("FILE_SERVER_ENDPOINT") or "http://0.0.0.0:6005"

logging.basicConfig(
    level=logging.INFO, format="%(levelname)s:     [%(asctime)s] %(message)s", datefmt="%d/%m/%Y %I:%M:%S"
)


def get_top_doc(top_n, videos) -> list:
    hit_score = {}
    if videos is None:
        return None
    for video_name in videos:
        try:
            if video_name not in hit_score.keys():
                hit_score[video_name] = 0
            hit_score[video_name] += 1
        except KeyError as r:
            logging.info(f"no video name {r}")

    x = dict(sorted(hit_score.items(), key=lambda item: -item[1]))  # sorted dict of video name and score
    top_n_names = list(x.keys())[:top_n]
    logging.info(f"top docs = {x}")
    logging.info(f"top n docs names = {top_n_names}")

    return top_n_names


def find_timestamp_from_video(metadata_list, video):
    return next(
        (metadata["timestamp"] for metadata in metadata_list if metadata["video"] == video),
        None,
    )


def format_video_name(video_name):
    # Check for an existing file extension
    match = re.search(r"\.(\w+)$", video_name)

    if match:
        extension = match.group(1)
        # If the extension is not 'mp4', raise an error
        if extension != "mp4":
            raise ValueError(f"Invalid file extension: .{extension}. Only '.mp4' is allowed.")

    # Use regex to remove any suffix after the base name (e.g., '_interval_0', etc.)
    base_name = re.sub(r"(_interval_\d+)?(\.mp4)?$", "", video_name)

    # Add the '.mp4' extension
    formatted_name = f"{base_name}.mp4"

    return formatted_name


@OpeaComponentRegistry.register("OPEA_VIDEO_RERANKING")
class OpeaVideoReranking(OpeaComponent):
    """A specialized reranking component derived from OpeaComponent for OPEA Video native reranking services."""

    def __init__(self, name: str, description: str, config: dict = None):
        super().__init__(name, ServiceType.RERANK.name.lower(), description, config)

    async def invoke(self, input: SearchedMultimodalDoc) -> LVMVideoDoc:
        """Invokes the reranking service to generate reranking for the provided input.

        Args:
            input (SearchedMultimodalDoc): The input in OpenAI reranking format.

        Returns:
            LVMVideoDoc: The response in OpenAI reranking format.
        """
        try:
            # get top video name from metadata
            video_names = [meta["video"] for meta in input.metadata]
            top_video_names = get_top_doc(input.top_n, video_names)

            # only use the first top video
            timestamp = find_timestamp_from_video(input.metadata, top_video_names[0])
            formatted_video_name = format_video_name(top_video_names[0])
            video_url = f"{file_server_endpoint.rstrip('/')}/{formatted_video_name}"

            result = LVMVideoDoc(
                video_url=video_url,
                prompt=input.initial_query,
                chunk_start=timestamp,
                chunk_duration=float(chunk_duration),
                max_new_tokens=512,
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            logging.error(f"Unexpected error in reranking: {str(e)}")
            # Handle any other exceptions with a generic server error response
            raise HTTPException(status_code=500, detail="An unexpected error occurred.")

        return result

    def check_health(self) -> bool:
        """Checks the health of the reranking service.

        Returns:
            bool: True if the service is reachable and healthy, False otherwise.
        """

        return True
