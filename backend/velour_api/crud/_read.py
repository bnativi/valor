import io
import json
from base64 import b64encode

from geoalchemy2 import RasterElement
from geoalchemy2.functions import (
    ST_Area,
    ST_AsGeoJSON,
    ST_AsPNG,
    ST_Boundary,
    ST_ConvexHull,
    ST_Count,
    ST_Envelope,
    ST_Polygon,
)
from PIL import Image
from sqlalchemy import Select, and_, func, select
from sqlalchemy.orm import Session

from velour_api import enums, exceptions, models, schemas


def _get_bounding_box_of_raster(
    db: Session, raster: RasterElement
) -> tuple[int, int, int, int]:
    env = json.loads(db.scalar(ST_AsGeoJSON(ST_Envelope(raster))))
    assert len(env["coordinates"]) == 1
    xs = [pt[0] for pt in env["coordinates"][0]]
    ys = [pt[1] for pt in env["coordinates"][0]]

    return min(xs), min(ys), max(xs), max(ys)


def _raster_to_png_b64(
    db: Session, raster: RasterElement, image: schemas.Image
) -> str:
    enveloping_box = _get_bounding_box_of_raster(db, raster)
    raster = Image.open(io.BytesIO(db.scalar(ST_AsPNG((raster))).tobytes()))

    assert raster.mode == "L"

    ret = Image.new(size=(image.width, image.height), mode=raster.mode)

    ret.paste(raster, box=enveloping_box)

    # mask is greyscale with values 0 and 1. to convert to binary
    # we first need to map 1 to 255
    ret = ret.point(lambda x: 255 if x == 1 else 0).convert("1")

    f = io.BytesIO()
    ret.save(f, format="PNG")
    f.seek(0)
    mask_bytes = f.read()
    return b64encode(mask_bytes).decode()


def get_datasets(db: Session) -> list[schemas.Dataset]:
    return [
        schemas.Dataset(name=d.name, draft=d.draft)
        for d in db.scalars(select(models.Dataset))
    ]


def get_dataset(db: Session, dataset_name: str) -> models.Dataset:
    ret = db.scalar(
        select(models.Dataset).where(models.Dataset.name == dataset_name)
    )
    if ret is None:
        raise exceptions.DatasetDoesNotExistError(dataset_name)

    return ret


def get_models(db: Session) -> list[schemas.Model]:
    return [
        schemas.Model(name=m.name) for m in db.scalars(select(models.Model))
    ]


def get_model(db: Session, model_name: str) -> models.Model:
    ret = db.scalar(
        select(models.Model).where(models.Model.name == model_name)
    )
    if ret is None:
        raise exceptions.ModelDoesNotExistError(model_name)

    return ret


def get_image(db: Session, uid: str, dataset_name: str) -> models.Image:
    ret = db.scalar(
        select(models.Image)
        .join(models.Dataset)
        .where(
            and_(
                models.Image.uid == uid,
                models.Image.dataset_id == models.Dataset.id,
                models.Dataset.name == dataset_name,
            )
        )
    )
    if ret is None:
        raise exceptions.ImageDoesNotExistError(uid, dataset_name)

    return ret


def _boundary_points_from_detection(
    db: Session,
    detection: models.PredictedDetection | models.GroundTruthDetection,
) -> list[tuple[float, float]]:
    geojson = db.scalar(ST_AsGeoJSON(detection.boundary))
    geojson = json.loads(geojson)
    coords = geojson["coordinates"]

    # make sure not a polygon
    assert len(coords) == 1

    return [tuple(coord) for coord in coords[0]]


def get_groundtruth_detections_in_image(
    db: Session, uid: str, dataset_name: str
) -> list[schemas.GroundTruthDetection]:
    db_img = get_image(db, uid, dataset_name)
    gt_dets = db_img.ground_truth_detections

    img = schemas.Image(
        uid=uid, height=db_img.height, width=db_img.width, frame=db_img.frame
    )

    def _single_db_gt_to_pydantic_gt(gt_det: models.GroundTruthDetection):
        labels = [
            _db_label_to_schemas_label(labeled_gt_det.label)
            for labeled_gt_det in gt_det.labeled_ground_truth_detections
        ]
        boundary = _boundary_points_from_detection(db, gt_det)

        if gt_det.is_bbox:
            xs = [b[0] for b in boundary]
            ys = [b[1] for b in boundary]
            return schemas.GroundTruthDetection(
                bbox=(min(xs), min(ys), max(xs), max(ys)),
                image=img,
                labels=labels,
            )
        else:
            return schemas.GroundTruthDetection(
                boundary=_boundary_points_from_detection(db, gt_det),
                image=img,
                labels=labels,
            )

    return [_single_db_gt_to_pydantic_gt(gt_det) for gt_det in gt_dets]


def get_groundtruth_segmentations_in_image(
    db: Session, uid: str, dataset_name: str, are_instance: bool
) -> list[schemas.GroundTruthSegmentation]:
    db_img = get_image(db, uid, dataset_name)
    gt_segs = db.scalars(
        select(models.GroundTruthSegmentation).where(
            and_(
                models.GroundTruthSegmentation.image_id == db_img.id,
                models.GroundTruthSegmentation.is_instance == are_instance,
            )
        )
    ).all()

    img = schemas.Image(
        uid=uid, height=db_img.height, width=db_img.width, frame=db_img.frame
    )

    return [
        schemas.GroundTruthSegmentation(
            shape=_raster_to_png_b64(db, gt_seg.shape, img),
            image=img,
            labels=[
                _db_label_to_schemas_label(labeled_gt_seg.label)
                for labeled_gt_seg in gt_seg.labeled_ground_truth_segmentations
            ],
            is_instance=gt_seg.is_instance,
        )
        for gt_seg in gt_segs
    ]


def get_labels_in_dataset(
    db: Session, dataset_name: str
) -> list[models.Label]:
    # TODO must be a better and more SQLy way of doing this
    dset = get_dataset(db, dataset_name)
    unique_ids = set()
    for image in dset.images:
        unique_ids.update(_get_unique_label_ids_in_image(image))

    return db.scalars(
        select(models.Label).where(models.Label.id.in_(unique_ids))
    ).all()


def get_all_labels(db: Session) -> list[schemas.Label]:
    return [
        schemas.Label(key=label.key, value=label.value)
        for label in db.scalars(select(models.Label))
    ]


def get_images_in_dataset(
    db: Session, dataset_name: str
) -> list[models.Image]:
    dset = get_dataset(db, dataset_name)
    return dset.images


def _get_unique_label_ids_in_image(image: models.Image) -> set[int]:
    ret = set()
    for det in image.ground_truth_detections:
        for labeled_det in det.labeled_ground_truth_detections:
            ret.add(labeled_det.label.id)

    for clf in image.ground_truth_classifications:
        ret.add(clf.label.id)

    for seg in image.ground_truth_segmentations:
        for labeled_seg in seg.labeled_ground_truth_segmentations:
            ret.add(labeled_seg.label.id)

    return ret


def _db_metric_params_to_pydantic_metric_params(
    metric_params: models.MetricParameters,
) -> schemas.MetricParameters:
    return schemas.MetricParameters(
        model_name=metric_params.model.name,
        dataset_name=metric_params.dataset.name,
        model_pred_task_type=metric_params.model_pred_task_type,
        dataset_gt_task_type=metric_params.dataset_gt_task_type,
    )


def _db_label_to_schemas_label(label: models.Label) -> schemas.Label:
    return schemas.Label(key=label.key, value=label.value)


def _db_metric_to_pydantic_metric(metric: models.APMetric) -> schemas.APMetric:
    # TODO: this will have to support more metrics
    return schemas.APMetric(
        iou=metric.iou,
        value=metric.value,
        label=_db_label_to_schemas_label(metric.label),
    )


def get_metrics_from_metric_params(
    metric_params: list[models.MetricParameters],
) -> list[schemas.MetricResponse]:
    return [
        schemas.MetricResponse(
            metric_name=m.__tablename__,
            parameters=_db_metric_params_to_pydantic_metric_params(mp),
            metric=_db_metric_to_pydantic_metric(m),
        )
        for mp in metric_params
        for m in mp.ap_metrics
    ]


def get_metrics_from_metric_params_id(
    db: Session, metric_params_id: int
) -> list[schemas.MetricResponse]:
    metric_params = db.scalar(
        select(models.MetricParameters).where(
            models.MetricParameters.id == metric_params_id
        )
    )
    return get_metrics_from_metric_params([metric_params])


def get_model_metrics(
    db: Session, model_name: str
) -> list[schemas.MetricResponse]:
    # TODO: may return multiple types of metrics
    # use get_model so exception get's raised if model does
    # not exist
    model = get_model(db, model_name)

    metric_params = db.scalars(
        select(models.MetricParameters)
        .join(models.Model)
        .where(models.Model.id == model.id)
    )

    return get_metrics_from_metric_params(metric_params)


def number_of_rows(db: Session, model_cls: type) -> int:
    return db.scalar(select(func.count(model_cls.id)))


def _filter_instance_segmentations_by_area(
    stmt: Select,
    seg_table: type,
    task_for_area_computation: schemas.Task,
    min_area: float | None,
    max_area: float | None,
) -> Select:
    if min_area is None and max_area is None:
        return stmt

    if task_for_area_computation == schemas.Task.BBOX_OBJECT_DETECTION:
        area_fn = lambda x: ST_Area(ST_Envelope(x))  # noqa: E731
    elif task_for_area_computation == schemas.Task.POLY_OBJECT_DETECTION:
        area_fn = lambda x: ST_Area(  # noqa: E731
            ST_ConvexHull(ST_Boundary(ST_Polygon(x)))
        )
    else:
        area_fn = ST_Count

    if min_area is not None:
        stmt = stmt.where(area_fn(seg_table.shape) >= min_area)
    if max_area is not None:
        stmt = stmt.where(area_fn(seg_table.shape) <= max_area)

    return stmt


def _instance_segmentations_in_dataset_statement(
    dataset_name: str,
    min_area: float = None,
    max_area: float = None,
    task_for_area_computation: schemas.Task = None,
) -> Select:
    """Produces the select statement to get all instance segmentations in a dataset,
    optionally filtered by area.

    Parameters
    ----------
    dataset_name
        name of the dataset
    min_area
        only select segmentations with area at least this value
    max_area
        only select segmentations with area at most this value
    task_for_area_computation
        one of Task.BBOX_OBJECT_DETECTION, Task.POLY_OBJECT_DETECTION, or
        Task.INSTANCE_SEGMENTATION. this determines how the area is calculated:
        if Task.BBOX_OBJECT_DETECTION then the area of the circumscribing polygon of the segmentation is used,
        if Task.POLY_OBJECT_DETECTION then the area of the convex hull of the segmentation is used
        if Task.INSTANCE_SEGMENTATION then the area of the segmentation itself is used.
    """
    return _filter_instance_segmentations_by_area(
        stmt=(
            select(models.LabeledGroundTruthSegmentation)
            .join(models.GroundTruthSegmentation)
            .join(models.Image)
            .join(models.Dataset)
            .where(
                and_(
                    models.GroundTruthSegmentation.is_instance,
                    models.Dataset.name == dataset_name,
                )
            )
        ),
        seg_table=models.GroundTruthSegmentation,
        task_for_area_computation=task_for_area_computation,
        min_area=min_area,
        max_area=max_area,
    )


def _filter_object_detections_by_area(
    stmt: Select,
    det_table: type,
    task_for_area_computation: schemas.Task | None,
    min_area: float | None,
    max_area: float | None,
) -> Select:
    if min_area is None and max_area is None:
        return stmt

    if task_for_area_computation == schemas.Task.BBOX_OBJECT_DETECTION:
        area_fn = lambda x: ST_Area(ST_Envelope(x))  # noqa: E731
    elif task_for_area_computation == schemas.Task.POLY_OBJECT_DETECTION:
        area_fn = ST_Area
    else:
        raise ValueError(
            f"Expected task_for_area_computation to be {schemas.Task.BBOX_OBJECT_DETECTION} or "
            f"{schemas.Task.POLY_OBJECT_DETECTION} but got {task_for_area_computation}."
        )

    if min_area is not None:
        stmt = stmt.where(area_fn(det_table.boundary) >= min_area)
    if max_area is not None:
        stmt = stmt.where(area_fn(det_table.boundary) <= max_area)

    return stmt


def _object_detections_in_dataset_statement(
    dataset_name: str,
    task: schemas.Task,
    min_area: float = None,
    max_area: float = None,
    task_for_area_computation: schemas.Task = None,
) -> Select:
    """returns the select statement for all groundtruth object detections in a dataset.
    if min_area and/or max_area is None then it will filter accordingly by the area (pixels^2 and not proportion)
    """
    if task not in [
        enums.Task.POLY_OBJECT_DETECTION,
        enums.Task.BBOX_OBJECT_DETECTION,
    ]:
        raise ValueError(
            f"Expected task to be a detection task but got {task}"
        )
    return _filter_object_detections_by_area(
        stmt=(
            select(models.LabeledGroundTruthDetection)
            .join(models.GroundTruthDetection)
            .join(models.Image)
            .join(models.Dataset)
            .where(
                and_(
                    models.Dataset.name == dataset_name,
                    models.GroundTruthDetection.is_bbox
                    == (task == enums.Task.BBOX_OBJECT_DETECTION),
                )
            )
        ),
        det_table=models.GroundTruthDetection,
        task_for_area_computation=task_for_area_computation,
        min_area=min_area,
        max_area=max_area,
    )


def _model_instance_segmentation_preds_statement(
    model_name: str,
    dataset_name: str,
    min_area: float = None,
    max_area: float = None,
    task_for_area_computation: schemas.Task = None,
) -> Select:
    return _filter_instance_segmentations_by_area(
        stmt=(
            select(models.LabeledPredictedSegmentation)
            .join(models.PredictedSegmentation)
            .join(models.Image)
            .join(models.Model)
            .join(models.Dataset)
            .where(
                and_(
                    models.Model.name == model_name,
                    models.Dataset.name == dataset_name,
                    models.PredictedSegmentation.is_instance,
                )
            )
        ),
        seg_table=models.PredictedSegmentation,
        task_for_area_computation=task_for_area_computation,
        min_area=min_area,
        max_area=max_area,
    )


def _model_object_detection_preds_statement(
    model_name: str,
    dataset_name: str,
    task: enums.Task,
    min_area: float = None,
    max_area: float = None,
    task_for_area_computation: schemas.Task = None,
) -> Select:
    if task not in [
        enums.Task.POLY_OBJECT_DETECTION,
        enums.Task.BBOX_OBJECT_DETECTION,
    ]:
        raise ValueError(
            f"Expected task to be a detection task but got {task}"
        )
    return _filter_object_detections_by_area(
        stmt=(
            select(models.LabeledPredictedDetection)
            .join(models.PredictedDetection)
            .join(models.Image)
            .join(models.Model)
            .join(models.Dataset)
            .where(
                and_(
                    models.Model.name == model_name,
                    models.Dataset.name == dataset_name,
                    models.PredictedDetection.is_bbox
                    == (task == enums.Task.BBOX_OBJECT_DETECTION),
                )
            )
        ),
        det_table=models.PredictedDetection,
        task_for_area_computation=task_for_area_computation,
        min_area=min_area,
        max_area=max_area,
    )


def get_dataset_task_types(db: Session, dataset_name: str) -> set[enums.Task]:
    ret = set()

    if db.query(
        _instance_segmentations_in_dataset_statement(
            dataset_name=dataset_name
        ).exists()
    ).scalar():
        ret.add(enums.Task.INSTANCE_SEGMENTATION)

    for task in [
        enums.Task.BBOX_OBJECT_DETECTION,
        enums.Task.POLY_OBJECT_DETECTION,
    ]:
        if db.query(
            _object_detections_in_dataset_statement(
                dataset_name, task
            ).exists()
        ).scalar():
            ret.add(task)

    return ret


def get_model_task_types(
    db: Session, model_name: str, dataset_name: str
) -> set[enums.Task]:
    ret = set()

    if db.query(
        _model_instance_segmentation_preds_statement(
            model_name=model_name, dataset_name=dataset_name
        ).exists()
    ).scalar():
        ret.add(enums.Task.INSTANCE_SEGMENTATION)

    for task in [
        enums.Task.BBOX_OBJECT_DETECTION,
        enums.Task.POLY_OBJECT_DETECTION,
    ]:
        if db.query(
            _model_object_detection_preds_statement(
                model_name=model_name, dataset_name=dataset_name, task=task
            ).exists()
        ).scalar():
            ret.add(task)

    return ret
