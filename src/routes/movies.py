import datetime
from math import ceil
from typing import Optional, Type

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from database import get_db, MovieModel
from database.models import (
    CountryModel,
    GenreModel,
    ActorModel,
    LanguageModel,
    MovieStatusEnum,
)
from schemas.movies import (
    MovieListResponseSchema,
    MovieDetailResponseSchema,
    MovieCreateRequestSchema,
    MovieUpdateRequestSchema,
    MessageResponseSchema,
)


router = APIRouter(prefix="/movies")


def _is_valid_status(value: str) -> bool:
    return value in {e.value for e in MovieStatusEnum}


def _validate_create_payload(payload: MovieCreateRequestSchema) -> Optional[str]:
    required = [
        payload.name, payload.date, payload.score, payload.overview, payload.status,
        payload.budget, payload.revenue, payload.country,
        payload.genres, payload.actors, payload.languages
    ]
    if any(v is None for v in required):
        return "Invalid input data."

    if not isinstance(payload.name, str) or len(payload.name) == 0 or len(payload.name) > 255:
        return "Invalid input data."

    if not isinstance(payload.overview, str) or len(payload.overview) == 0:
        return "Invalid input data."

    if not _is_valid_status(payload.status):
        return "Invalid input data."

    today = datetime.date.today()
    if payload.date > today + datetime.timedelta(days=365):
        return "Invalid input data."

    if payload.score < 0 or payload.score > 100:
        return "Invalid input data."

    if payload.budget < 0 or payload.revenue < 0:
        return "Invalid input data."

    if not isinstance(payload.country, str) or len(payload.country) == 0:
        return "Invalid input data."

    if (not isinstance(payload.genres, list) 
            or not isinstance(payload.actors, list) 
            or not isinstance(payload.languages, list)):
        return "Invalid input data."

    if any((not isinstance(x, str)) or len(x.strip()) == 0 for x in payload.genres):
        return "Invalid input data."
    if any((not isinstance(x, str)) or len(x.strip()) == 0 for x in payload.actors):
        return "Invalid input data."
    if any((not isinstance(x, str)) or len(x.strip()) == 0 for x in payload.languages):
        return "Invalid input data."

    return None


def _validate_update_payload(payload: MovieUpdateRequestSchema) -> Optional[str]:
    if all(
        getattr(payload, f) is None
        for f in ("name", "date", "score", "overview", "status", "budget", "revenue")
    ):
        return "Invalid input data."

    if payload.name is not None and (len(payload.name) == 0 or len(payload.name) > 255):
        return "Invalid input data."

    if payload.date is not None:
        today = datetime.date.today()
        if payload.date > today + datetime.timedelta(days=365):
            return "Invalid input data."

    if payload.score is not None and (payload.score < 0 or payload.score > 100):
        return "Invalid input data."

    if payload.budget is not None and payload.budget < 0:
        return "Invalid input data."

    if payload.revenue is not None and payload.revenue < 0:
        return "Invalid input data."

    if payload.status is not None and not _is_valid_status(payload.status):
        return "Invalid input data."

    if payload.overview is not None and len(payload.overview) == 0:
        return "Invalid input data."

    return None


async def _get_or_create_by_unique_str(
    db: AsyncSession,
    model: Type,
    field_name: str,
    value: str,
):
    stmt = select(model).where(getattr(model, field_name) == value)
    result = await db.execute(stmt)
    obj = result.scalars().first()
    if obj is not None:
        return obj

    obj = model(**{field_name: value})
    db.add(obj)
    await db.flush()
    return obj


async def _get_or_create_country(db: AsyncSession, code: str) -> CountryModel:
    stmt = select(CountryModel).where(CountryModel.code == code)
    result = await db.execute(stmt)
    country = result.scalars().first()
    if country is not None:
        return country

    country = CountryModel(code=code, name=None)
    db.add(country)
    await db.flush()
    return country


@router.get("/", response_model=MovieListResponseSchema)
async def get_movies(
    page: int = Query(1, ge=1),
    per_page: int = Query(10, ge=1, le=20),
    db: AsyncSession = Depends(get_db),
):
    total_items = await db.scalar(select(func.count(MovieModel.id)))
    total_items = int(total_items or 0)

    if total_items == 0:
        raise HTTPException(status_code=404, detail="No movies found.")

    total_pages = (total_items + per_page - 1) // per_page
    offset = (page - 1) * per_page

    stmt = (
        select(MovieModel)
        .order_by(MovieModel.id.desc())
        .offset(offset)
        .limit(per_page)
    )
    result = await db.execute(stmt)
    movies = result.scalars().all()

    if not movies:
        raise HTTPException(status_code=404, detail="No movies found.")

    base_path = "/theater/movies/"

    prev_page = f"{base_path}?page={page - 1}&per_page={per_page}" if page > 1 else None
    next_page = f"{base_path}?page={page + 1}&per_page={per_page}" if page < total_pages else None

    return {
        "movies": movies,
        "prev_page": prev_page,
        "next_page": next_page,
        "total_pages": total_pages,
        "total_items": total_items,
    }


@router.get("/{movie_id}/", response_model=MovieDetailResponseSchema)
async def get_movie_by_id(
    movie_id: int,
    db: AsyncSession = Depends(get_db),
):
    stmt = (
        select(MovieModel)
        .where(MovieModel.id == movie_id)
        .options(
            joinedload(MovieModel.country),
            joinedload(MovieModel.genres),
            joinedload(MovieModel.actors),
            joinedload(MovieModel.languages),
        )
    )
    result = await db.execute(stmt)
    movie = result.scalars().first()

    if movie is None:
        raise HTTPException(status_code=404,
                            detail="Movie with the given ID was not found.")

    return movie


@router.post("/", status_code=201, response_model=MovieDetailResponseSchema)
async def create_movie(
    payload: MovieCreateRequestSchema,
    db: AsyncSession = Depends(get_db),
):
    err = _validate_create_payload(payload)
    if err is not None:
        raise HTTPException(status_code=400, detail=err)

    try:
        country = await _get_or_create_country(db, payload.country)

        genres = []
        for g in payload.genres or []:
            genres.append(await _get_or_create_by_unique_str(db, GenreModel, "name", g.strip()))

        actors = []
        for a in payload.actors or []:
            actors.append(await _get_or_create_by_unique_str(db, ActorModel, "name", a.strip()))

        languages = []
        for lang in payload.languages or []:
            languages.append(
                await _get_or_create_by_unique_str(db, LanguageModel, "name", lang.strip())
            )

        movie = MovieModel(
            name=payload.name,
            date=payload.date,
            score=payload.score,
            overview=payload.overview,
            status=MovieStatusEnum(payload.status),
            budget=payload.budget,
            revenue=payload.revenue,
            country=country,
            genres=genres,
            actors=actors,
            languages=languages,
        )

        db.add(movie)
        await db.commit()

    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail=(
                f"A movie with the name '{payload.name}' "
                f"and release date '{payload.date.isoformat()}' already exists."
            ),
        )
    except Exception:
        await db.rollback()
        raise HTTPException(status_code=400, detail="Invalid input data.")

    stmt = (
        select(MovieModel)
        .where(MovieModel.id == movie.id)
        .options(
            joinedload(MovieModel.country),
            joinedload(MovieModel.genres),
            joinedload(MovieModel.actors),
            joinedload(MovieModel.languages),
        )
    )
    result = await db.execute(stmt)
    created = result.scalars().first()
    return created


@router.delete("/{movie_id}/", status_code=204)
async def delete_movie(
    movie_id: int,
    db: AsyncSession = Depends(get_db),
):
    movie = await db.get(MovieModel, movie_id)
    if movie is None:
        raise HTTPException(status_code=404,
                            detail="Movie with the given ID was not found.")

    await db.delete(movie)
    await db.commit()
    return None


@router.patch("/{movie_id}/", response_model=MessageResponseSchema)
async def update_movie(
    movie_id: int,
    payload: MovieUpdateRequestSchema,
    db: AsyncSession = Depends(get_db),
):
    movie = await db.get(MovieModel, movie_id)
    if movie is None:
        raise HTTPException(status_code=404,
                            detail="Movie with the given ID was not found.")

    err = _validate_update_payload(payload)
    if err is not None:
        raise HTTPException(status_code=400, detail=err)

    # обновляем только то, что пришло
    if payload.name is not None:
        movie.name = payload.name

    if payload.date is not None:
        movie.date = payload.date

    if payload.score is not None:
        movie.score = payload.score

    if payload.overview is not None:
        movie.overview = payload.overview

    if payload.status is not None:
        movie.status = MovieStatusEnum(payload.status)

    if payload.budget is not None:
        movie.budget = payload.budget

    if payload.revenue is not None:
        movie.revenue = payload.revenue

    try:
        await db.commit()
    except Exception:
        await db.rollback()
        raise HTTPException(status_code=400, detail="Invalid input data.")

    return {"detail": "Movie updated successfully."}
