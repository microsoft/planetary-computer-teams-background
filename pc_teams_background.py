#!/usr/bin/python
import argparse
import io
import json
import os
import random
import sys
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Union
from uuid import uuid4

import dateparser
import pystac
import requests
import yaml
from PIL import Image, ImageOps
from PIL.Image import Image as PILImage
from pydantic import BaseModel, validator
from pystac_client import Client
from shapely.geometry import box, mapping, shape

AOI_LAST_ITEM_DT_KEY = "last_item_datetime"


class SettingsError(Exception):
    pass


class FilterConfig(BaseModel):
    property: str
    op: str
    value: Any

    def to_cql_op(self) -> Dict[str, Any]:
        return {"op": self.op, "args": [{"property": self.property}, self.value]}


class CollectionConfig(BaseModel):
    id: str
    rendering_option: Optional[str] = None
    search_days: int = 30
    filters: Optional[List[FilterConfig]] = None


class AOIsConfig(BaseModel):
    feature_collection_path: str
    refresh_days: int = 1

    @validator("feature_collection_path")
    def _validate_fc_path(cls, v: str) -> str:
        if not Path(v).exists():
            raise ValueError(f"Feature collection path {v} does not exist")
        return v


class APIURLConfig(BaseModel):
    stac: str
    info: str
    image: str


class Settings(BaseModel):
    image_name: str = "pc-teams-background.png"
    teams_image_folder: str
    collections: List[CollectionConfig]
    width: int
    height: int
    thumbnail_width: int
    thumbnail_height: int
    apis: APIURLConfig
    max_search_results: int = 1000
    aois: Optional[AOIsConfig] = None
    image_info_path: Optional[str] = None
    force_regen_after: Optional[str] = None
    mirror_image: bool = False

    def get_image_path(self) -> Path:
        return Path(self.teams_image_folder) / self.image_name

    def get_thumbnail_path(self) -> Path:
        return Path(self.teams_image_folder) / (
            Path(self.image_name).stem + "_thumbnail.jpg"
        )

    def get_image_info_path(self) -> Path:
        if self.image_info_path:
            return Path(self.image_info_path)
        else:
            p = Path(self.teams_image_folder)
            return p.joinpath(f"{Path(self.image_name).stem}-info.json")

    def get_force_regen_after_time(self, created_at: datetime) -> Optional[datetime]:
        if self.force_regen_after is None:
            return None
        dt = dateparser.parse(f"{self.force_regen_after} ago")
        assert dt
        delta = datetime.now() - dt
        return created_at + delta

    def get_collection_config(self, collection_id: str) -> CollectionConfig:
        result = next(filter(lambda c: c.id == collection_id, self.collections), None)
        if not result:
            raise ValueError(f"Collection {collection_id} not found")
        return result

    @validator("force_regen_after")
    def _validate_force_regen_after(cls, v: str) -> str:
        if v is not None:
            x = dateparser.parse(f"{v} ago")
            if not x:
                raise ValueError(f"Invalid force regen after date phrase: {v}")
        return v

    @classmethod
    def from_yaml(cls, yaml_path: Union[str, Path]) -> "Settings":
        with open(yaml_path) as f:
            settings = yaml.safe_load(f)
        return cls(**settings)

    @classmethod
    def load(cls) -> "Settings":
        if os.environ.get("PC_TEAMS_BG_SETTINGS_FILE"):
            path = Path(os.environ["PC_TEAMS_BG_SETTINGS_FILE"])
        else:
            HERE = Path(__file__).parent
            path = HERE / "settings.yaml"
        return cls.from_yaml(path)


class ImageInfo(BaseModel):
    target_item: Dict[str, Any]
    cql: Dict[str, Any]
    render_params: str
    is_aoi: bool
    last_changed: Optional[datetime] = None

    @classmethod
    def from_yaml(cls, yaml_path: Union[str, Path]) -> "ImageInfo":
        with open(yaml_path) as f:
            settings = yaml.safe_load(f)
        return cls(**settings)


def cql_add_geom_arg(cql: Dict[str, Any], geom: Dict[str, Any]):
    result = deepcopy(cql)
    result["filter"]["args"].append(
        {"op": "s_intersects", "args": [{"property": "geometry"}, geom]}
    )
    return result


def cql_add_after_arg(cql: Dict[str, Any], after: str) -> Dict[str, Any]:
    result = deepcopy(cql)
    result["filter"]["args"].append(
        {
            "op": "anyinteracts",
            "args": [
                {"property": "datetime"},
                {"interval": [after, datetime.utcnow().isoformat()]},
            ],
        }
    )
    return result


def ensure_ids(fc_path: Path) -> None:
    """Ensure a feature collection has IDs set.

    If not, set them and write out the file.
    """
    with open(fc_path) as f:
        feature_collection = json.load(f)
    write = False
    for feature in feature_collection["features"]:
        if "id" not in feature:
            write = True
            feature["id"] = str(uuid4())
    if write:
        with open(fc_path, "w") as f:
            json.dump(feature_collection, f, indent=2)


def get_datetime(item: pystac.Item) -> datetime:
    dt = item.datetime or item.common_metadata.start_datetime
    if not dt:
        raise ValueError(f"Item {item.id} has no datetime")
    return dt


class TeamsBackgroundGenerator:
    def __init__(self, settings: Settings, force: bool = False):
        self.settings = settings
        self.force = force

    def should_generate_new_background(self) -> bool:
        image_path = self.settings.get_image_path()
        if not image_path.exists():
            print(f"Image {image_path} does not exist, creating...")
            return True

        image_info_path = self.settings.get_image_info_path()
        if image_info_path.exists():
            image_info = ImageInfo.from_yaml(image_info_path)
            if image_info.is_aoi and self.settings.aois:
                last_changed = image_info.last_changed
                if (
                    last_changed
                    and (datetime.now() - last_changed).days
                    < self.settings.aois.refresh_days
                ):
                    print("Not regenerating AOI image because AOI item is recent")
                    return False

        stats = image_path.stat()
        created_at = datetime.fromtimestamp(stats.st_ctime, tz=timezone.utc)
        accessed_at = datetime.fromtimestamp(stats.st_atime, tz=timezone.utc)
        if (accessed_at - created_at).total_seconds() > 2:
            print("Background image has been read after creating; regenerating")
            return True
        else:
            force_regen_after_time = self.settings.get_force_regen_after_time(
                created_at
            )
            if (
                force_regen_after_time
                and datetime.now(tz=timezone.utc) > force_regen_after_time
            ):
                print(
                    "Background image has not regenerated in a while, regenerating..."
                )
                return True
            else:
                print("Background image has not been read after creating.")
                return False

    def get_base_cql(
        self, collection_id: str, additional_filters: Optional[List[FilterConfig]]
    ) -> Dict[str, Any]:
        return {
            "filter-lang": "cql2-json",
            "filter": {
                "op": "and",
                "args": [
                    {"op": "=", "args": [{"property": "collection"}, collection_id]},
                ]
                + [f.to_cql_op() for f in (additional_filters or [])],
            },
        }

    def get_bg_geom(self, base_geom: Dict[str, Any]) -> Dict[str, Any]:
        geom = shape(base_geom)
        envelope = geom.envelope
        bounds: List[float] = list(envelope.bounds)
        width: float = bounds[2] - bounds[0]
        height: float = bounds[3] - bounds[1]
        new_height: float = width * (self.settings.height / self.settings.width)
        height_diff: float = new_height - height
        xmin: float = bounds[0]
        xmax: float = bounds[2]
        ymin: float = bounds[1] - (height_diff / 2)
        ymax: float = bounds[3] + (height_diff / 2)

        return mapping(box(xmin, ymin, xmax, ymax))

    def get_render_params(
        self, collection_id: str, render_options_name: Optional[str] = None
    ) -> str:
        resp = requests.get(f"{self.settings.apis.info}?collection={collection_id}")
        mosaic_info = resp.json()
        if render_options_name:
            for render_options in mosaic_info["render_options"]:
                if render_options["name"] == render_options_name:
                    return render_options["options"]
        return mosaic_info["renderOptions"][0]["options"]

    def fetch_image(self, request_data: Dict[str, Any]) -> PILImage:
        resp = requests.post(self.settings.apis.image, json=request_data)
        resp.raise_for_status()
        resp_json = resp.json()
        bytes = requests.get(resp_json["url"]).content
        return Image.open(io.BytesIO(bytes))

    def get_target_items(self) -> List[pystac.Item]:
        client = Client.open(self.settings.apis.stac)
        target_aoi_items: List[pystac.Item] = []
        target_random_items: List[pystac.Item] = []

        for collection_config in self.settings.collections:
            collection_id = collection_config.id
            search_after = (
                datetime.utcnow() - timedelta(days=collection_config.search_days)
            ).isoformat()

            base_cql = self.get_base_cql(collection_id, collection_config.filters)

            if self.settings.aois:
                fc_path = Path(self.settings.aois.feature_collection_path)

                print("Finding items that intersect AOIs...")
                features = json.loads(fc_path.read_text())["features"]
                for feature in features:
                    aoi_id = feature["id"]
                    aoi_cql = cql_add_geom_arg(base_cql, feature["geometry"])
                    aoi_cql = cql_add_after_arg(aoi_cql, search_after)
                    aoi_item = next(
                        client.search(
                            filter=aoi_cql,
                            max_items=self.settings.max_search_results,
                        ).items(),
                        None,
                    )
                    if aoi_item:
                        # Check if the item was already used.
                        this_dt = get_datetime(aoi_item)
                        last_dt: Optional[datetime] = None
                        if AOI_LAST_ITEM_DT_KEY in feature["properties"]:
                            last_dt = datetime.fromisoformat(
                                feature["properties"].get(AOI_LAST_ITEM_DT_KEY)
                            )
                        if not last_dt or this_dt > last_dt:
                            print(f"Found new item that intersects AOI {aoi_id}...")
                            aoi_item.properties["aoi"] = aoi_id
                            aoi_item.properties["aoi_geom"] = feature["geometry"]
                            target_aoi_items.append(aoi_item)

            if not target_aoi_items:
                print("Finding random items...")
                items = client.search(
                    filter=cql_add_after_arg(base_cql, search_after),
                    max_items=self.settings.max_search_results,
                ).get_all_items()

                if not items:
                    print(f"WARNING: No items found. Skipping {collection_id}.")

                print(f"Found {len(items)} items")
                if len(items) == self.settings.max_search_results:
                    print("(limit hit)")

                target_random_items.extend(items)

        return target_aoi_items or target_random_items

    def set_aoi_item_info(self, item: pystac.Item) -> None:
        # Set the properties of an AOI to
        if self.settings.aois:
            aoi_id = item.properties["aoi"]
            item_dt = get_datetime(item)
            fc_path = self.settings.aois.feature_collection_path
            with open(fc_path) as f:
                feature_collection = json.load(f)
            for feature in feature_collection["features"]:
                if feature["id"] == aoi_id:
                    feature["properties"][AOI_LAST_ITEM_DT_KEY] = item_dt.isoformat()
            with open(fc_path, "w") as f:
                json.dump(feature_collection, f, indent=2)

    def generate(self) -> bool:
        if self.force:
            print("Forcing regeneration...")
        else:
            if not self.should_generate_new_background():
                print("No need to generate new background")
                return False

        fc_path: Optional[Path] = None
        if self.settings.aois:
            fc_path = Path(self.settings.aois.feature_collection_path)
            ensure_ids(fc_path)

        image_info: Optional[ImageInfo] = None
        image_info_path = self.settings.get_image_info_path()
        if image_info_path.exists():
            image_info = ImageInfo.parse_file(image_info_path)

        target_item = random.choice(self.get_target_items())
        is_aoi = False
        if target_item.properties.get("aoi"):
            is_aoi = True
            self.set_aoi_item_info(target_item)
            target_geom: Dict[str, Any] = target_item.properties["aoi_geom"]
        else:
            if not target_item.geometry:
                raise Exception(f"Item {target_item.id} has no geometry")
            target_geom = target_item.geometry

        if not target_item:
            raise Exception("ERROR: No target item found!")

        collection_id = target_item.collection_id
        assert collection_id

        collection_config = self.settings.get_collection_config(collection_id)
        render_options = collection_config.rendering_option

        print("Generating background image...")
        bg_geom = self.get_bg_geom(target_geom)
        render_params = self.get_render_params(collection_id, render_options)
        cql = self.get_base_cql(collection_id, collection_config.filters)
        cql = cql_add_geom_arg(cql, bg_geom)

        request_data: Dict[str, Any] = {
            "cql": cql,
            "render_params": render_params + f"&collection={collection_id}",
            "cols": self.settings.width,
            "rows": self.settings.height,
        }

        image = self.fetch_image(request_data)
        bg_image = image
        if self.settings.mirror_image:
            bg_image = ImageOps.mirror(bg_image)
        bg_image.convert("RGB").save(self.settings.get_image_path())
        thumbnail = image.resize(
            (self.settings.thumbnail_width, self.settings.thumbnail_height)
        )
        thumbnail.convert("RGB").save(self.settings.get_thumbnail_path())

        print("Writing info...")
        image_info = ImageInfo(
            target_item=target_item.to_dict(),
            cql=cql,
            render_params=render_params,
            is_aoi=is_aoi,
            last_changed=datetime.now(),
        )
        with open(self.settings.get_image_info_path(), "w") as f:
            f.write(image_info.json(indent=2))
        print("Done.")
        return True


if __name__ == "__main__":
    arg_parser = argparse.ArgumentParser()
    arg_parser.add_argument("-f", "--force", action="store_true")
    arg_parser.add_argument("-d", "--debug", action="store_true")

    args = arg_parser.parse_args()
    settings = Settings.load()
    generator = TeamsBackgroundGenerator(settings, args.force)
    try:
        generator.generate()
    except Exception as e:
        if args.debug:
            raise
        print(f"ERROR: {e}")
        sys.exit(1)
