#!/usr/bin/env python3
"""
TicketFlow Load Lab
===================

Real-user journey load-test console for TicketFlow.

Journey:
    REGISTER
      -> LOGIN
      -> BROWSE EVENTS
      -> OPEN EVENT
      -> LOAD SEATS
      -> SELECT AVAILABLE SEAT
      -> BOOK
      -> VIEW ORDERS
      -> OPEN FOOD MENU
      -> OPTIONAL FOOD ORDER
      -> SIMULATE PAYMENT
      -> CHECK NOTIFICATIONS

Install:
    python3 -m pip install flask requests

Run:
    python3 ticketflow_load_test.py

Open:
    http://127.0.0.1:5000

WARNING:
    This creates real users, bookings, food orders and payments.
    Use a dedicated test dataset/database.
"""

from __future__ import annotations

import csv
import html
import json
import math
import os
import random
import statistics
import threading
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from flask import Flask, jsonify, render_template_string, request, send_file


APP = Flask(__name__)
REPORT_DIR = Path(__file__).resolve().parent / "ticketflow_reports"
REPORT_DIR.mkdir(parents=True, exist_ok=True)

LOCK = threading.Lock()
STOP_EVENT = threading.Event()
RUN_THREAD: threading.Thread | None = None
THREAD_LOCAL = threading.local()

DEFAULTS = {
    "base_url": "http://34.0.5.74/api",
    "users": 100,
    "events": 10,
    "seats_per_event": 20,
    "user_concurrency": 20,
    "registration_concurrency": 20,
    "timeout": 15.0,
    "scenario": "distributed",
    "think_time_min": 0.10,
    "think_time_max": 0.50,
    "food_probability": 0.35,
    "payment_success_probability": 1.0,
    "email_domain": "example.com",
    "email_prefix": "ticketflow-load",
    "password": "LoadTest@2026!",
}

STATE: dict[str, Any] = {
    "running": False,
    "phase": "IDLE",
    "started_at": None,
    "finished_at": None,
    "config": {},
    "catalog": [],
    "registration": {},
    "booking": {},
    "journey": {},
    "journey_results": [],
    "integrity": {},
    "users_prepared": 0,
    "report_json": None,
    "report_csv": None,
    "report_html": None,
    "error": None,
}


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_phase_metrics() -> dict[str, Any]:
    return {
        "attempts": 0,
        "success": 0,
        "errors": 0,
        "latencies_ms": [],
        "status_codes": Counter(),
    }


def reset_state(config: dict[str, Any]) -> None:
    with LOCK:
        STATE["running"] = True
        STATE["phase"] = "STARTING"
        STATE["started_at"] = time.time()
        STATE["finished_at"] = None
        STATE["config"] = dict(config)
        STATE["catalog"] = []
        STATE["registration"] = {
            **new_phase_metrics(),
            "conflict": 0,
            "client_error": 0,
            "server_error": 0,
            "timeout": 0,
            "network_error": 0,
            "other_error": 0,
            "duration_seconds": 0.0,
        }
        STATE["booking"] = {
            **new_phase_metrics(),
            "conflict": 0,
            "client_error": 0,
            "server_error": 0,
            "timeout": 0,
            "network_error": 0,
            "other_error": 0,
            "duration_seconds": 0.0,
            "event_stats": defaultdict(
                lambda: {
                    "event_name": "",
                    "attempts": 0,
                    "success": 0,
                    "conflict": 0,
                    "errors": 0,
                }
            ),
            "failures_sample": [],
        }
        STATE["journey"] = {
            "users_started": 0,
            "users_completed": 0,
            "users_failed": 0,
            "total_latencies_ms": [],
            "login": new_phase_metrics(),
            "browse_events": new_phase_metrics(),
            "event_detail": new_phase_metrics(),
            "seat_selection": new_phase_metrics(),
            "orders": new_phase_metrics(),
            "food": new_phase_metrics(),
            "payment": {
                **new_phase_metrics(),
                "failed": 0,
            },
            "notifications": new_phase_metrics(),
        }
        STATE["journey_results"] = []
        STATE["integrity"] = {
            "checked": False,
            "expected_success": 0,
            "actual_booked_seats": 0,
            "mismatches": [],
        }
        STATE["users_prepared"] = 0
        STATE["report_json"] = None
        STATE["report_csv"] = None
        STATE["report_html"] = None
        STATE["error"] = None


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * p / 100
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (
        ordered[upper] - ordered[lower]
    ) * (position - lower)


def latency_summary(values: list[float]) -> dict[str, float]:
    if not values:
        return {
            "avg_ms": 0.0,
            "min_ms": 0.0,
            "p50_ms": 0.0,
            "p95_ms": 0.0,
            "p99_ms": 0.0,
            "max_ms": 0.0,
        }
    return {
        "avg_ms": statistics.mean(values),
        "min_ms": min(values),
        "p50_ms": percentile(values, 50),
        "p95_ms": percentile(values, 95),
        "p99_ms": percentile(values, 99),
        "max_ms": max(values),
    }


def safe_rate(success: int, attempts: int) -> float:
    return (success / attempts * 100) if attempts else 0.0


def get_session() -> requests.Session:
    session = getattr(THREAD_LOCAL, "session", None)
    if session is None:
        session = requests.Session()
        adapter = requests.adapters.HTTPAdapter(
            pool_connections=100,
            pool_maxsize=100,
            max_retries=0,
        )
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        THREAD_LOCAL.session = session
    return session


def api_request(
    method: str,
    url: str,
    *,
    timeout: float,
    **kwargs: Any,
) -> tuple[requests.Response | None, float | None, str | None]:
    started = time.perf_counter()
    try:
        response = get_session().request(
            method,
            url,
            timeout=timeout,
            **kwargs,
        )
        return response, (time.perf_counter() - started) * 1000, None
    except requests.Timeout:
        return None, None, "TIMEOUT"
    except requests.RequestException as exc:
        return None, None, f"NETWORK_ERROR: {exc}"


def classify_response(
    metrics: dict[str, Any],
    response: requests.Response | None,
    latency_ms: float | None,
    error: str | None,
) -> str:
    with LOCK:
        metrics["attempts"] += 1
        if latency_ms is not None:
            metrics["latencies_ms"].append(latency_ms)

        if response is not None:
            code = response.status_code
            metrics["status_codes"][str(code)] += 1

            if 200 <= code < 300:
                metrics["success"] += 1
                return "success"

            if code == 409 and "conflict" in metrics:
                metrics["conflict"] += 1
                return "conflict"

            if code == 408 and "timeout" in metrics:
                metrics["timeout"] += 1
                return "timeout"

            if 400 <= code < 500 and "client_error" in metrics:
                metrics["client_error"] += 1
                return "client_error"

            if code >= 500 and "server_error" in metrics:
                metrics["server_error"] += 1
                return "server_error"

            metrics["errors"] += 1
            if "other_error" in metrics:
                metrics["other_error"] += 1
            return "error"

        metrics["status_codes"][error or "UNKNOWN"] += 1

        if error == "TIMEOUT" and "timeout" in metrics:
            metrics["timeout"] += 1
        elif error and error.startswith("NETWORK_ERROR") and "network_error" in metrics:
            metrics["network_error"] += 1
        elif "other_error" in metrics:
            metrics["other_error"] += 1

        metrics["errors"] += 1
        return "error"


def record_step(
    name: str,
    response: requests.Response | None,
    latency_ms: float | None,
    error: str | None,
) -> str:
    return classify_response(
        STATE["journey"][name],
        response,
        latency_ms,
        error,
    )


def think_time(config: dict[str, Any]) -> None:
    low = max(0.0, float(config["think_time_min"]))
    high = max(low, float(config["think_time_max"]))
    if high > 0:
        time.sleep(random.uniform(low, high))


def get_catalog(
    base_url: str,
    events_count: int,
    seats_per_event: int,
    timeout: float,
) -> list[dict[str, Any]]:
    response, _, error = api_request(
        "GET",
        f"{base_url}/v1/events/",
        timeout=timeout,
    )

    if response is None:
        raise RuntimeError(f"Cannot reach events API: {error}")

    response.raise_for_status()
    events = response.json()

    if len(events) < events_count:
        raise RuntimeError(
            f"Requested {events_count} events, but API returned {len(events)}."
        )

    catalog = []

    for event in events[:events_count]:
        event_id = event["id"]

        response, _, error = api_request(
            "GET",
            f"{base_url}/v1/seats/event/{event_id}",
            timeout=timeout,
        )

        if response is None:
            raise RuntimeError(
                f"Cannot read seats for event {event_id}: {error}"
            )

        response.raise_for_status()
        seats = response.json()

        available = [
            seat for seat in seats
            if seat.get("status") == "AVAILABLE"
        ]

        if not available:
            raise RuntimeError(
                f"Event {event_id} has no available seats."
            )

        catalog.append({
            "event_id": event_id,
            "event_name": event.get("name", f"Event {event_id}"),
            "seats": available[:seats_per_event],
        })

    return catalog


def register_user(
    base_url: str,
    index: int,
    config: dict[str, Any],
) -> dict[str, Any] | None:
    email = (
        f"{config['email_prefix']}-{index:05d}"
        f"@{config['email_domain']}"
    )

    response, latency_ms, error = api_request(
        "POST",
        f"{base_url}/v1/auth/register",
        timeout=config["timeout"],
        json={
            "name": f"Load Test User {index:05d}",
            "email": email,
            "password": config["password"],
        },
    )

    classify_response(
        STATE["registration"],
        response,
        latency_ms,
        error,
    )

    if response is None or response.status_code != 201:
        return None

    return {
        "email": email,
        "name": f"Load Test User {index:05d}",
    }


def login_user(
    user: dict[str, Any],
    config: dict[str, Any],
) -> dict[str, Any] | None:
    response, latency_ms, error = api_request(
        "POST",
        f"{config['base_url']}/v1/auth/login",
        timeout=config["timeout"],
        json={
            "email": user["email"],
            "password": config["password"],
        },
    )

    if record_step("login", response, latency_ms, error) != "success":
        return None

    try:
        body = response.json()
        return {
            **user,
            "user_id": body["user_id"],
            "token": body["access_token"],
        }
    except (ValueError, KeyError):
        return None


def choose_target(
    user_index: int,
    catalog: list[dict[str, Any]],
    scenario: str,
) -> dict[str, Any]:
    if scenario == "hotspot":
        return catalog[0]
    if scenario == "random":
        return random.choice(catalog)
    return catalog[user_index % len(catalog)]


def real_user_journey(
    user: dict[str, Any],
    user_index: int,
    catalog: list[dict[str, Any]],
    config: dict[str, Any],
) -> dict[str, Any]:
    started = time.perf_counter()

    result: dict[str, Any] = {
        "user_index": user_index,
        "email": user["email"],
        "status": "FAILED",
        "event_id": None,
        "event_name": None,
        "seat_id": None,
        "booking_id": None,
        "booking_reference": None,
        "food_order_id": None,
        "payment_id": None,
        "steps": {},
        "total_latency_ms": 0.0,
        "error": None,
    }

    with LOCK:
        STATE["journey"]["users_started"] += 1

    try:
        # 1. LOGIN
        authenticated = login_user(user, config)
        if authenticated is None:
            result["steps"]["login"] = "FAILED"
            return result

        user = authenticated
        result["steps"]["login"] = "SUCCESS"

        think_time(config)

        headers = {
            "Authorization": f"Bearer {user['token']}",
            "Content-Type": "application/json",
        }

        # 2. BROWSE EVENTS
        response, latency_ms, error = api_request(
            "GET",
            f"{config['base_url']}/v1/events/",
            timeout=config["timeout"],
        )

        if record_step(
            "browse_events", response, latency_ms, error
        ) != "success":
            result["steps"]["browse_events"] = "FAILED"
            return result

        result["steps"]["browse_events"] = "SUCCESS"

        think_time(config)

        # 3. SELECT EVENT
        target = choose_target(
            user_index,
            catalog,
            config["scenario"],
        )

        event_id = target["event_id"]
        result["event_id"] = event_id
        result["event_name"] = target["event_name"]

        # 4. OPEN EVENT
        response, latency_ms, error = api_request(
            "GET",
            f"{config['base_url']}/v1/events/{event_id}",
            timeout=config["timeout"],
        )

        if record_step(
            "event_detail", response, latency_ms, error
        ) != "success":
            result["steps"]["event_detail"] = "FAILED"
            return result

        result["steps"]["event_detail"] = "SUCCESS"

        think_time(config)

        # 5. LOAD SEAT MAP
        response, latency_ms, error = api_request(
            "GET",
            f"{config['base_url']}/v1/seats/event/{event_id}",
            timeout=config["timeout"],
        )

        if record_step(
            "seat_selection", response, latency_ms, error
        ) != "success":
            result["steps"]["seat_selection"] = "FAILED"
            return result

        seats = response.json()
        available = [
            seat for seat in seats
            if seat.get("status") == "AVAILABLE"
        ]

        if not available:
            result["steps"]["seat_selection"] = "NO_AVAILABLE_SEAT"
            return result

        # User selects from the CURRENT seat map, not from the
        # preloaded contention pool.
        seat = random.choice(available)

        result["seat_id"] = seat["id"]
        result["steps"]["seat_selection"] = "SUCCESS"

        think_time(config)

        # 6. BOOK NOW
        response, latency_ms, error = api_request(
            "POST",
            f"{config['base_url']}/v1/bookings/",
            timeout=config["timeout"],
            headers=headers,
            json={
                "event_id": event_id,
                "seat_ids": [seat["id"]],
            },
        )

        classification = classify_response(
            STATE["booking"],
            response,
            latency_ms,
            error,
        )

        with LOCK:
            event_metrics = STATE["booking"]["event_stats"][event_id]
            event_metrics["event_name"] = target["event_name"]
            event_metrics["attempts"] += 1

            if classification == "success":
                event_metrics["success"] += 1
            elif classification == "conflict":
                event_metrics["conflict"] += 1
            else:
                event_metrics["errors"] += 1

        if classification != "success":
            result["steps"]["booking"] = classification.upper()

            if (
                classification != "conflict"
                and len(STATE["booking"]["failures_sample"]) < 100
            ):
                body = response.text[:500] if response is not None else ""
                with LOCK:
                    STATE["booking"]["failures_sample"].append({
                        "user_id": user["user_id"],
                        "event_id": event_id,
                        "event_name": target["event_name"],
                        "seat_id": seat["id"],
                        "classification": classification,
                        "status_code": (
                            response.status_code
                            if response is not None
                            else error
                        ),
                        "latency_ms": latency_ms,
                        "response": body,
                    })

            return result

        booking = response.json()
        result["booking_id"] = booking["id"]
        result["booking_reference"] = booking["reference"]
        result["steps"]["booking"] = "SUCCESS"

        think_time(config)

        # 7. VIEW MY ORDERS
        response, latency_ms, error = api_request(
            "GET",
            f"{config['base_url']}/v1/bookings/orders",
            timeout=config["timeout"],
            headers=headers,
        )

        result["steps"]["orders"] = (
            "SUCCESS"
            if record_step("orders", response, latency_ms, error) == "success"
            else "FAILED"
        )

        think_time(config)

        # 8. OPEN FOOD MENU
        response, latency_ms, error = api_request(
            "GET",
            f"{config['base_url']}/v1/food/items",
            timeout=config["timeout"],
            headers=headers,
        )

        food_result = record_step(
            "food", response, latency_ms, error
        )

        if food_result != "success":
            result["steps"]["food"] = "FAILED"
        else:
            food_items = [
                item for item in response.json()
                if item.get("available", True)
            ]

            if (
                food_items
                and random.random() < config["food_probability"]
            ):
                food = random.choice(food_items)

                think_time(config)

                response, latency_ms, error = api_request(
                    "POST",
                    f"{config['base_url']}/v1/food/orders",
                    timeout=config["timeout"],
                    headers=headers,
                    json={
                        "booking_id": result["booking_id"],
                        "items": [[food["id"], 1]],
                    },
                )

                if record_step(
                    "food", response, latency_ms, error
                ) == "success":
                    body = response.json()
                    result["food_order_id"] = body.get("order_id")
                    result["steps"]["food"] = "SUCCESS"
                else:
                    result["steps"]["food"] = "FAILED"
            else:
                result["steps"]["food"] = "SKIPPED"

        # 9. PAYMENT
        think_time(config)

        payment_result = (
            "success"
            if random.random() < config["payment_success_probability"]
            else "failed"
        )

        response, latency_ms, error = api_request(
            "POST",
            f"{config['base_url']}/v1/payments/simulate/{result['booking_id']}",
            timeout=config["timeout"],
            headers=headers,
            params={"result": payment_result},
        )

        if payment_result == "success":
            classification = record_step(
                "payment", response, latency_ms, error
            )

            if classification == "success":
                result["steps"]["payment"] = "SUCCESS"
                try:
                    result["payment_id"] = response.json()["payment"]["id"]
                except (ValueError, KeyError, TypeError):
                    pass
            else:
                result["steps"]["payment"] = "FAILED"
        else:
            with LOCK:
                STATE["journey"]["payment"]["attempts"] += 1
                if latency_ms is not None:
                    STATE["journey"]["payment"]["latencies_ms"].append(
                        latency_ms
                    )
                if response is not None:
                    STATE["journey"]["payment"]["status_codes"][
                        str(response.status_code)
                    ] += 1
                STATE["journey"]["payment"]["failed"] += 1
            result["steps"]["payment"] = "SIMULATED_FAILURE"

        # 10. CHECK NOTIFICATIONS
        think_time(config)

        response, latency_ms, error = api_request(
            "GET",
            f"{config['base_url']}/v1/notifications/",
            timeout=config["timeout"],
            headers=headers,
        )

        result["steps"]["notifications"] = (
            "SUCCESS"
            if record_step(
                "notifications",
                response,
                latency_ms,
                error,
            ) == "success"
            else "FAILED"
        )

        result["status"] = "SUCCESS"
        return result

    except Exception as exc:
        result["error"] = str(exc)
        return result

    finally:
        total_ms = (time.perf_counter() - started) * 1000
        result["total_latency_ms"] = total_ms

        with LOCK:
            STATE["journey"]["total_latencies_ms"].append(total_ms)
            if result["status"] == "SUCCESS":
                STATE["journey"]["users_completed"] += 1
            else:
                STATE["journey"]["users_failed"] += 1


def verify_data_integrity(
    catalog: list[dict[str, Any]],
    config: dict[str, Any],
) -> None:
    expected_success = STATE["booking"]["success"]
    actual_booked = 0
    mismatches = []

    for target in catalog:
        response, _, error = api_request(
            "GET",
            f"{config['base_url']}/v1/seats/event/{target['event_id']}",
            timeout=config["timeout"],
        )

        if response is None or response.status_code != 200:
            mismatches.append({
                "event_id": target["event_id"],
                "error": error or (
                    f"HTTP {response.status_code}"
                    if response is not None
                    else "unknown"
                ),
            })
            continue

        seats = response.json()
        target_ids = {seat["id"] for seat in target["seats"]}

        booked = sum(
            1
            for seat in seats
            if seat["id"] in target_ids
            and seat.get("status") == "BOOKED"
        )

        actual_booked += booked

        expected_event_success = (
            STATE["booking"]["event_stats"]
            [target["event_id"]]["success"]
        )

        if booked != expected_event_success:
            mismatches.append({
                "event_id": target["event_id"],
                "event_name": target["event_name"],
                "expected_booked": expected_event_success,
                "actual_booked": booked,
            })

    with LOCK:
        STATE["integrity"] = {
            "checked": True,
            "expected_success": expected_success,
            "actual_booked_seats": actual_booked,
            "mismatches": mismatches,
        }


def clean_metrics(metrics: dict[str, Any]) -> dict[str, Any]:
    result = {}
    for key, value in metrics.items():
        if key in {"latencies_ms", "status_codes"}:
            continue
        result[key] = value
    result["status_codes"] = dict(metrics.get("status_codes", {}))
    result["latency"] = latency_summary(
        list(metrics.get("latencies_ms", []))
    )
    result["success_rate"] = safe_rate(
        metrics.get("success", 0),
        metrics.get("attempts", 0),
    )
    return result


def build_snapshot() -> dict[str, Any]:
    with LOCK:
        started = STATE["started_at"]
        finished = STATE["finished_at"]
        elapsed = (
            (finished or time.time()) - started
            if started
            else 0.0
        )

        booking = STATE["booking"]
        registration = STATE["registration"]
        journey = STATE["journey"]

        journey_latency = latency_summary(
            list(journey["total_latencies_ms"])
        )

        booking_latency = latency_summary(
            list(booking["latencies_ms"])
        )

        return {
            "running": STATE["running"],
            "phase": STATE["phase"],
            "started_at": STATE["started_at"],
            "finished_at": STATE["finished_at"],
            "elapsed_seconds": elapsed,
            "config": {
                **STATE["config"],
                "password": "***",
            },
            "users_prepared": STATE["users_prepared"],
            "registration": clean_metrics(registration),
            "booking": {
                **clean_metrics(booking),
                "throughput_rps": (
                    booking["attempts"] / booking["duration_seconds"]
                    if booking["duration_seconds"]
                    else 0.0
                ),
                "event_stats": {
                    str(event_id): dict(stats)
                    for event_id, stats
                    in booking["event_stats"].items()
                },
                "failures_sample": list(
                    booking["failures_sample"]
                ),
            },
            "journey": {
                "users_started": journey["users_started"],
                "users_completed": journey["users_completed"],
                "users_failed": journey["users_failed"],
                "completion_rate": safe_rate(
                    journey["users_completed"],
                    journey["users_started"],
                ),
                "latency": journey_latency,
                "steps": {
                    name: clean_metrics(metrics)
                    for name, metrics in journey.items()
                    if isinstance(metrics, dict)
                    and "attempts" in metrics
                },
            },
            "integrity": dict(STATE["integrity"]),
            "report_json": STATE["report_json"],
            "report_csv": STATE["report_csv"],
            "report_html": STATE["report_html"],
            "error": STATE["error"],
        }


def render_report_html(report: dict[str, Any]) -> str:
    results = report["results"]
    booking = results["booking"]
    journey = results["journey"]
    integrity = results["integrity"]

    event_rows = "".join(
        f"""
        <tr>
            <td>{html.escape(str(event_id))}</td>
            <td>{html.escape(str(stats["event_name"]))}</td>
            <td>{stats["attempts"]}</td>
            <td>{stats["success"]}</td>
            <td>{stats["conflict"]}</td>
            <td>{stats["errors"]}</td>
        </tr>
        """
        for event_id, stats in booking["event_stats"].items()
    )

    step_rows = ""
    for name, metrics in journey["steps"].items():
        latency = metrics["latency"]
        step_rows += f"""
        <tr>
            <td>{html.escape(name)}</td>
            <td>{metrics["attempts"]}</td>
            <td>{metrics["success"]}</td>
            <td>{metrics["errors"]}</td>
            <td>{latency["p50_ms"]:.1f} ms</td>
            <td>{latency["p95_ms"]:.1f} ms</td>
        </tr>
        """

    status_class = "ok" if not integrity["mismatches"] else "bad"

    return f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>TicketFlow Real User Load Report</title>
<style>
body {{
    margin:0;
    background:#f4f7fb;
    color:#162033;
    font-family:Inter,Arial,sans-serif;
}}
main {{
    max-width:1200px;
    margin:40px auto;
    padding:0 24px;
}}
header,.panel {{
    background:white;
    border:1px solid #dfe5ef;
    border-radius:14px;
    padding:22px;
    margin-bottom:18px;
}}
header {{
    background:#111a2e;
    color:white;
}}
.grid {{
    display:grid;
    grid-template-columns:repeat(4,1fr);
    gap:12px;
}}
.card {{
    background:white;
    border:1px solid #dfe5ef;
    border-radius:12px;
    padding:18px;
}}
.value {{
    font-size:27px;
    font-weight:700;
    margin-top:6px;
}}
.muted {{ color:#68758a; font-size:13px; }}
table {{ width:100%; border-collapse:collapse; }}
th,td {{
    padding:10px;
    border-bottom:1px solid #e7ebf2;
    text-align:left;
}}
.ok {{ color:#14804a; font-weight:700; }}
.bad {{ color:#c73737; font-weight:700; }}
@media(max-width:900px) {{
    .grid {{ grid-template-columns:repeat(2,1fr); }}
}}
@media(max-width:600px) {{
    .grid {{ grid-template-columns:1fr; }}
}}
</style>
</head>
<body>
<main>
<header>
    <h1>TicketFlow Load Lab</h1>
    <div>Real-user journey benchmark</div>
    <div class="muted" style="color:#aeb9cc;margin-top:8px">
        Generated: {html.escape(report["report"]["generated_at_utc"])}
    </div>
</header>

<section class="grid">
    <div class="card">
        <div class="muted">Users completed</div>
        <div class="value">{journey["users_completed"]:,}</div>
    </div>
    <div class="card">
        <div class="muted">Booking attempts</div>
        <div class="value">{booking["attempts"]:,}</div>
    </div>
    <div class="card">
        <div class="muted">Successful bookings</div>
        <div class="value">{booking["success"]:,}</div>
    </div>
    <div class="card">
        <div class="muted">409 conflicts</div>
        <div class="value">{booking["conflict"]:,}</div>
    </div>
</section>

<section class="panel">
<h2>Real User Journey</h2>
<table>
<tr>
<th>Step</th><th>Attempts</th><th>Success</th>
<th>Errors</th><th>P50</th><th>P95</th>
</tr>
{step_rows}
</table>
</section>

<section class="panel">
<h2>Booking Performance</h2>
<p>Throughput: <strong>{booking["throughput_rps"]:.2f} req/s</strong></p>
<p>
Average: {booking["latency"]["avg_ms"]:.2f} ms |
P50: {booking["latency"]["p50_ms"]:.2f} ms |
P95: {booking["latency"]["p95_ms"]:.2f} ms |
P99: {booking["latency"]["p99_ms"]:.2f} ms
</p>
</section>

<section class="panel">
<h2>Data Integrity</h2>
<p>
Expected successful bookings:
<strong>{integrity["expected_success"]}</strong>
</p>
<p>
Actual booked seats:
<strong>{integrity["actual_booked_seats"]}</strong>
</p>
<p class="{status_class}">
{
    "PASS — booking result matches seat state"
    if not integrity["mismatches"]
    else "FAIL — seat-state mismatches detected"
}
</p>
</section>

<section class="panel">
<h2>Per Event</h2>
<table>
<tr>
<th>Event ID</th><th>Event</th><th>Attempts</th>
<th>Success</th><th>409</th><th>Errors</th>
</tr>
{event_rows}
</table>
</section>

<section class="panel">
<h2>HTTP Status Codes</h2>
<pre>{html.escape(json.dumps(booking["status_codes"], indent=2))}</pre>
</section>
</main>
</body>
</html>"""


def create_reports(catalog: list[dict[str, Any]]) -> None:
    snapshot = build_snapshot()

    raw_report = {
        "report": {
            "name": "TicketFlow Real User Load Report",
            "generated_at_utc": now_utc(),
            "tool": "ticketflow_load_test.py",
        },
        "configuration": snapshot["config"],
        "catalog": [
            {
                "event_id": item["event_id"],
                "event_name": item["event_name"],
                "target_seat_count": len(item["seats"]),
                "target_seat_ids": [
                    seat["id"] for seat in item["seats"]
                ],
            }
            for item in catalog
        ],
        "results": snapshot,
        "journey_results": list(STATE["journey_results"]),
        "failure_sample": list(
            STATE["booking"]["failures_sample"]
        ),
    }

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = REPORT_DIR / f"ticketflow_{stamp}.json"
    csv_path = REPORT_DIR / f"ticketflow_{stamp}.csv"
    html_path = REPORT_DIR / f"ticketflow_{stamp}.html"

    json_path.write_text(
        json.dumps(raw_report, indent=2, default=str),
        encoding="utf-8",
    )

    with csv_path.open(
        "w",
        newline="",
        encoding="utf-8",
    ) as file:
        writer = csv.writer(file)

        writer.writerow([
            "user_index",
            "email",
            "status",
            "event_id",
            "event_name",
            "seat_id",
            "booking_id",
            "booking_reference",
            "food_order_id",
            "payment_id",
            "total_latency_ms",
        ])

        for item in STATE["journey_results"]:
            writer.writerow([
                item.get("user_index"),
                item.get("email"),
                item.get("status"),
                item.get("event_id"),
                item.get("event_name"),
                item.get("seat_id"),
                item.get("booking_id"),
                item.get("booking_reference"),
                item.get("food_order_id"),
                item.get("payment_id"),
                item.get("total_latency_ms"),
            ])

        writer.writerow([])
        writer.writerow(["metric", "value"])
        writer.writerow([
            "users_completed",
            snapshot["journey"]["users_completed"],
        ])
        writer.writerow([
            "journey_completion_rate",
            snapshot["journey"]["completion_rate"],
        ])
        writer.writerow([
            "booking_attempts",
            snapshot["booking"]["attempts"],
        ])
        writer.writerow([
            "booking_success",
            snapshot["booking"]["success"],
        ])
        writer.writerow([
            "booking_conflict_409",
            snapshot["booking"]["conflict"],
        ])
        writer.writerow([
            "booking_throughput_rps",
            snapshot["booking"]["throughput_rps"],
        ])

    html_path.write_text(
        render_report_html(raw_report),
        encoding="utf-8",
    )

    with LOCK:
        STATE["report_json"] = json_path.name
        STATE["report_csv"] = csv_path.name
        STATE["report_html"] = html_path.name


def run_test(config: dict[str, Any]) -> None:
    try:
        with LOCK:
            STATE["phase"] = "CATALOG"

        catalog = get_catalog(
            config["base_url"],
            config["events"],
            config["seats_per_event"],
            config["timeout"],
        )

        with LOCK:
            STATE["catalog"] = catalog
            STATE["phase"] = "REGISTERING"

        # Registration is preparation. The actual load is generated
        # by the complete customer journey below.
        users: list[dict[str, Any]] = []
        registration_started = time.perf_counter()

        with ThreadPoolExecutor(
            max_workers=config["registration_concurrency"]
        ) as executor:
            futures = {
                executor.submit(
                    register_user,
                    config["base_url"],
                    index,
                    config,
                ): index
                for index in range(1, config["users"] + 1)
            }

            for future in as_completed(futures):
                if STOP_EVENT.is_set():
                    break

                result = future.result()

                with LOCK:
                    STATE["users_prepared"] += 1

                if result is not None:
                    users.append(result)

        with LOCK:
            STATE["registration"]["duration_seconds"] = (
                time.perf_counter() - registration_started
            )

        if STOP_EVENT.is_set():
            raise RuntimeError("Test stopped by operator.")

        if not users:
            raise RuntimeError("No users were registered successfully.")

        with LOCK:
            STATE["phase"] = "REAL_USER_JOURNEY"

        journey_started = time.perf_counter()

        with ThreadPoolExecutor(
            max_workers=config["user_concurrency"]
        ) as executor:
            futures = {
                executor.submit(
                    real_user_journey,
                    user,
                    index,
                    catalog,
                    config,
                ): index
                for index, user in enumerate(users, start=1)
            }

            for future in as_completed(futures):
                if STOP_EVENT.is_set():
                    break

                result = future.result()

                with LOCK:
                    STATE["journey_results"].append(result)

        with LOCK:
            STATE["booking"]["duration_seconds"] = (
                time.perf_counter() - journey_started
            )
            STATE["phase"] = "VERIFYING"

        verify_data_integrity(catalog, config)

        with LOCK:
            STATE["phase"] = "COMPLETED"

        create_reports(catalog)

    except Exception as exc:
        with LOCK:
            STATE["phase"] = "FAILED"
            STATE["error"] = str(exc)

    finally:
        with LOCK:
            STATE["running"] = False
            STATE["finished_at"] = time.time()


PAGE = r"""
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>TicketFlow Real User Load Lab</title>
<style>
:root {
    --bg:#080d18;
    --panel:#101827;
    --panel2:#0c1422;
    --border:#22304a;
    --text:#edf3ff;
    --muted:#8492aa;
    --green:#37d99a;
    --red:#ff6b6b;
    --yellow:#f4c95d;
    --blue:#66a3ff;
}
* { box-sizing:border-box; }
body {
    margin:0;
    background:var(--bg);
    color:var(--text);
    font-family:Inter,ui-sans-serif,system-ui,sans-serif;
}
.shell {
    max-width:1450px;
    margin:auto;
    padding:28px;
}
.topbar {
    display:flex;
    justify-content:space-between;
    align-items:center;
    margin-bottom:24px;
}
.brand {
    display:flex;
    align-items:center;
    gap:12px;
}
.logo {
    width:42px;
    height:42px;
    border-radius:11px;
    display:grid;
    place-items:center;
    background:#18243a;
    border:1px solid var(--border);
    font-weight:800;
}
h1,h2 { margin:0; }
h1 { font-size:24px; }
h2 { font-size:16px; margin-bottom:16px; }
.subtitle,.muted { color:var(--muted); }
.badge {
    padding:7px 12px;
    border:1px solid var(--border);
    border-radius:999px;
    font-size:12px;
}
.panel {
    background:var(--panel);
    border:1px solid var(--border);
    border-radius:14px;
    padding:20px;
    margin-bottom:18px;
}
.grid {
    display:grid;
    grid-template-columns:repeat(4,minmax(0,1fr));
    gap:12px;
}
.kpis {
    display:grid;
    grid-template-columns:repeat(6,minmax(0,1fr));
    gap:10px;
}
.kpi {
    background:var(--panel2);
    border:1px solid var(--border);
    border-radius:11px;
    padding:15px;
}
.kpi .label { color:var(--muted); font-size:12px; }
.kpi .value { font-size:23px; font-weight:750; margin-top:7px; }
.field label {
    display:block;
    color:var(--muted);
    font-size:12px;
    margin-bottom:7px;
}
input,select {
    width:100%;
    border:1px solid #2a3a57;
    border-radius:9px;
    padding:11px 12px;
    background:#0a1220;
    color:var(--text);
    outline:none;
}
.actions {
    display:flex;
    gap:10px;
    margin-top:18px;
}
button {
    border:0;
    border-radius:9px;
    padding:11px 17px;
    font-weight:700;
    cursor:pointer;
}
.primary { background:var(--green); color:#06140e; }
.danger { background:#a93645; color:white; }
button:disabled { opacity:.45; cursor:not-allowed; }
.progress {
    height:8px;
    border-radius:999px;
    background:#172237;
    overflow:hidden;
}
.progress > div {
    height:100%;
    width:0%;
    background:var(--green);
    transition:width .2s;
}
.two {
    display:grid;
    grid-template-columns:1fr 1fr;
    gap:18px;
}
table {
    width:100%;
    border-collapse:collapse;
}
th,td {
    padding:10px 8px;
    border-bottom:1px solid var(--border);
    text-align:left;
    font-size:13px;
}
th { color:var(--muted); font-weight:600; }
pre {
    background:#080e19;
    border:1px solid var(--border);
    border-radius:9px;
    padding:13px;
    overflow:auto;
    max-height:260px;
    color:#b9c7df;
}
.links a {
    color:#80b5ff;
    text-decoration:none;
    margin-right:18px;
}
.ok { color:var(--green); }
.bad { color:var(--red); }
.warn { color:var(--yellow); }
.notice {
    padding:13px 15px;
    border-radius:9px;
    background:#171b18;
    border:1px solid #514b2a;
    color:#d8ca86;
    font-size:13px;
    line-height:1.5;
    margin-bottom:18px;
}
@media(max-width:1100px) {
    .kpis { grid-template-columns:repeat(3,1fr); }
    .grid { grid-template-columns:repeat(2,1fr); }
}
@media(max-width:700px) {
    .kpis,.grid,.two { grid-template-columns:1fr; }
    .shell { padding:16px; }
}
</style>
</head>
<body>
<div class="shell">

<div class="topbar">
    <div class="brand">
        <div class="logo">TF</div>
        <div>
            <h1>TicketFlow Load Lab</h1>
            <div class="subtitle">Real customer journey benchmark</div>
        </div>
    </div>
    <div id="status" class="badge">IDLE</div>
</div>

<div class="notice">
<strong>Real-data test.</strong>
This creates real users and real booking/payment/food data.
Use a dedicated TicketFlow test dataset.
</div>

<section class="panel">
<h2>Test Configuration</h2>

<div class="grid">

<div class="field">
<label>API base URL</label>
<input id="base_url" value="__BASE_URL__">
</div>

<div class="field">
<label>Virtual users</label>
<input id="users" type="number" value="100" min="1" max="10000">
</div>

<div class="field">
<label>Events</label>
<input id="events" type="number" value="10" min="1" max="100">
</div>

<div class="field">
<label>Target seats / event</label>
<input id="seats" type="number" value="20" min="1" max="100">
</div>

<div class="field">
<label>User concurrency</label>
<input id="user_concurrency" type="number" value="20" min="1" max="2000">
</div>

<div class="field">
<label>Registration concurrency</label>
<input id="registration_concurrency" type="number" value="20" min="1" max="500">
</div>

<div class="field">
<label>Think time min (sec)</label>
<input id="think_time_min" type="number" value="0.10" min="0" max="10" step="0.05">
</div>

<div class="field">
<label>Think time max (sec)</label>
<input id="think_time_max" type="number" value="0.50" min="0" max="10" step="0.05">
</div>

<div class="field">
<label>Food probability</label>
<input id="food_probability" type="number" value="0.35" min="0" max="1" step="0.05">
</div>

<div class="field">
<label>Request timeout (seconds)</label>
<input id="timeout" type="number" value="15" min="1" max="120">
</div>

<div class="field">
<label>Traffic scenario</label>
<select id="scenario">
<option value="distributed">Distributed — all events</option>
<option value="hotspot">Hotspot — one event</option>
<option value="random">Random — all events</option>
</select>
</div>

<div class="field">
<label>Test email domain</label>
<input id="email_domain" value="example.com">
</div>

<div class="field">
<label>Test email prefix</label>
<input id="email_prefix" value="ticketflow-load">
</div>

</div>

<div class="actions">
<button id="start" class="primary" onclick="startTest()">START REAL USER TEST</button>
<button id="stop" class="danger" onclick="stopTest()" disabled>STOP</button>
</div>
</section>

<section class="panel">
<h2>Execution</h2>
<div id="phase" class="muted">Waiting for test...</div>
<div style="margin-top:10px" class="progress">
<div id="progress"></div>
</div>
<div style="margin-top:9px" class="muted" id="progress_text">0%</div>
</section>

<section class="panel">
<h2>Journey</h2>
<div class="kpis">
<div class="kpi"><div class="label">Users started</div><div id="users_started" class="value">0</div></div>
<div class="kpi"><div class="label">Completed</div><div id="users_completed" class="value">0</div></div>
<div class="kpi"><div class="label">Failed</div><div id="users_failed" class="value">0</div></div>
<div class="kpi"><div class="label">Completion</div><div id="completion" class="value">0%</div></div>
<div class="kpi"><div class="label">Journey P95</div><div id="journey_p95" class="value">0 ms</div></div>
<div class="kpi"><div class="label">Booking attempts</div><div id="attempts" class="value">0</div></div>
</div>
</section>

<section class="panel">
<h2>Booking</h2>
<div class="kpis">
<div class="kpi"><div class="label">Success</div><div id="success" class="value">0</div></div>
<div class="kpi"><div class="label">409 Conflict</div><div id="conflict" class="value">0</div></div>
<div class="kpi"><div class="label">5xx</div><div id="server_error" class="value">0</div></div>
<div class="kpi"><div class="label">Timeouts</div><div id="timeout" class="value">0</div></div>
<div class="kpi"><div class="label">Throughput</div><div id="throughput" class="value">0 req/s</div></div>
<div class="kpi"><div class="label">P95</div><div id="p95" class="value">0 ms</div></div>
</div>
</section>

<section class="panel">
<h2>Journey Steps</h2>
<table>
<thead>
<tr>
<th>Step</th>
<th>Attempts</th>
<th>Success</th>
<th>Errors</th>
<th>P50</th>
<th>P95</th>
</tr>
</thead>
<tbody id="journey_steps"></tbody>
</table>
</section>

<div class="two">

<section class="panel">
<h2>HTTP Status</h2>
<pre id="codes">{}</pre>
</section>

<section class="panel">
<h2>Data Integrity</h2>
<div id="integrity" class="muted">Not checked yet.</div>
</section>

</div>

<section class="panel">
<h2>Per Event</h2>
<table>
<thead>
<tr>
<th>Event</th>
<th>Attempts</th>
<th>Success</th>
<th>409</th>
<th>Errors</th>
</tr>
</thead>
<tbody id="events"></tbody>
</table>
</section>

<section class="panel">
<h2>Reports</h2>
<div id="reports" class="links muted">Reports appear after completion.</div>
<div id="error" class="bad" style="margin-top:12px"></div>
</section>

</div>

<script>
let timer = null;

const $ = id => document.getElementById(id);
const n = id => Number($(id).value);

function formatNumber(value) {
    return Number(value || 0).toLocaleString();
}

function formatMs(value) {
    return Number(value || 0).toFixed(1) + " ms";
}

async function startTest() {
    const payload = {
        base_url: $("base_url").value.trim(),
        users: n("users"),
        events: n("events"),
        seats_per_event: n("seats"),
        user_concurrency: n("user_concurrency"),
        registration_concurrency: n("registration_concurrency"),
        think_time_min: n("think_time_min"),
        think_time_max: n("think_time_max"),
        food_probability: n("food_probability"),
        timeout: n("timeout"),
        scenario: $("scenario").value,
        email_domain: $("email_domain").value.trim(),
        email_prefix: $("email_prefix").value.trim()
    };

    const response = await fetch("/api/start", {
        method:"POST",
        headers:{"Content-Type":"application/json"},
        body:JSON.stringify(payload)
    });

    const body = await response.json();

    if (!response.ok) {
        alert(body.error || "Unable to start test");
        return;
    }

    $("start").disabled = true;
    $("stop").disabled = false;
    poll();
}

async function stopTest() {
    await fetch("/api/stop", {method:"POST"});
    $("stop").disabled = true;
}

function render(s) {
    $("status").textContent = s.phase;
    $("phase").textContent =
        s.phase + " · " +
        s.users_prepared.toLocaleString() +
        " / " +
        s.config.users.toLocaleString() +
        " users prepared";

    const users = Number(s.config.users || 1);
    const prepared = Number(s.users_prepared || 0);

    $("progress").style.width =
        Math.min(100, prepared / users * 100) + "%";

    $("progress_text").textContent =
        Math.min(100, prepared / users * 100).toFixed(1) + "%";

    const j = s.journey || {};
    const b = s.booking || {};

    $("users_started").textContent =
        formatNumber(j.users_started);

    $("users_completed").textContent =
        formatNumber(j.users_completed);

    $("users_failed").textContent =
        formatNumber(j.users_failed);

    $("completion").textContent =
        Number(j.completion_rate || 0).toFixed(1) + "%";

    $("journey_p95").textContent =
        formatMs(j.latency?.p95_ms);

    $("attempts").textContent =
        formatNumber(b.attempts);

    $("success").textContent =
        formatNumber(b.success);

    $("conflict").textContent =
        formatNumber(b.conflict);

    $("server_error").textContent =
        formatNumber(b.server_error);

    $("timeout").textContent =
        formatNumber(b.timeout);

    $("throughput").textContent =
        Number(b.throughput_rps || 0).toFixed(2) + " req/s";

    $("p95").textContent =
        formatMs(b.latency?.p95_ms);

    $("codes").textContent =
        JSON.stringify(b.status_codes || {}, null, 2);

    const integrity = s.integrity;

    if (integrity.checked) {
        if (integrity.mismatches.length === 0) {
            $("integrity").innerHTML =
                '<span class="ok">PASS</span> — ' +
                integrity.expected_success +
                ' successful booking responses match ' +
                integrity.actual_booked_seats +
                ' booked seats.';
        } else {
            $("integrity").innerHTML =
                '<span class="bad">FAIL</span> — ' +
                integrity.mismatches.length +
                ' mismatch(es) detected.';
        }
    } else {
        $("integrity").textContent = "Verification pending...";
    }

    const tbody = $("events");
    tbody.innerHTML = "";

    for (const [id,e] of Object.entries(b.event_stats || {})) {
        const row = document.createElement("tr");
        row.innerHTML =
            "<td>" + id + " · " + e.event_name + "</td>" +
            "<td>" + formatNumber(e.attempts) + "</td>" +
            "<td>" + formatNumber(e.success) + "</td>" +
            "<td>" + formatNumber(e.conflict) + "</td>" +
            "<td>" + formatNumber(e.errors) + "</td>";
        tbody.appendChild(row);
    }

    const steps = $("journey_steps");
    steps.innerHTML = "";

    for (const [name,m] of Object.entries(j.steps || {})) {
        const row = document.createElement("tr");
        row.innerHTML =
            "<td>" + name + "</td>" +
            "<td>" + formatNumber(m.attempts) + "</td>" +
            "<td>" + formatNumber(m.success) + "</td>" +
            "<td>" + formatNumber(m.errors) + "</td>" +
            "<td>" + formatMs(m.latency?.p50_ms) + "</td>" +
            "<td>" + formatMs(m.latency?.p95_ms) + "</td>";
        steps.appendChild(row);
    }

    $("error").textContent = s.error || "";

    if (s.report_json) {
        $("reports").innerHTML =
            '<a href="/report/json" target="_blank">JSON</a>' +
            '<a href="/report/csv">CSV</a>' +
            '<a href="/report/html" target="_blank">HTML</a>';
    }

    if (!s.running) {
        $("start").disabled = false;
        $("stop").disabled = true;

        if (timer) {
            clearInterval(timer);
            timer = null;
        }
    }
}

async function poll() {
    const response = await fetch("/api/status");
    const body = await response.json();
    render(body);

    if (body.running) {
        if (!timer) {
            timer = setInterval(poll, 1000);
        }
    }
}
</script>
</body>
</html>
"""


@APP.get("/")
def index():
    return render_template_string(
        PAGE,
        base_url=DEFAULTS["base_url"],
    )


@APP.post("/api/start")
def start_test():
    global RUN_THREAD

    with LOCK:
        if STATE["running"]:
            return jsonify({"error": "A test is already running."}), 409

    payload = request.get_json(silent=True) or {}

    try:
        config = {
            "base_url": str(
                payload.get("base_url", DEFAULTS["base_url"])
            ).rstrip("/"),
            "users": int(payload.get("users", DEFAULTS["users"])),
            "events": int(payload.get("events", DEFAULTS["events"])),
            "seats_per_event": int(
                payload.get(
                    "seats_per_event",
                    DEFAULTS["seats_per_event"],
                )
            ),
            "user_concurrency": int(
                payload.get(
                    "user_concurrency",
                    DEFAULTS["user_concurrency"],
                )
            ),
            "registration_concurrency": int(
                payload.get(
                    "registration_concurrency",
                    DEFAULTS["registration_concurrency"],
                )
            ),
            "timeout": float(
                payload.get("timeout", DEFAULTS["timeout"])
            ),
            "scenario": str(
                payload.get("scenario", DEFAULTS["scenario"])
            ),
            "think_time_min": float(
                payload.get(
                    "think_time_min",
                    DEFAULTS["think_time_min"],
                )
            ),
            "think_time_max": float(
                payload.get(
                    "think_time_max",
                    DEFAULTS["think_time_max"],
                )
            ),
            "food_probability": float(
                payload.get(
                    "food_probability",
                    DEFAULTS["food_probability"],
                )
            ),
            "payment_success_probability": float(
                payload.get(
                    "payment_success_probability",
                    DEFAULTS["payment_success_probability"],
                )
            ),
            "email_domain": str(
                payload.get(
                    "email_domain",
                    DEFAULTS["email_domain"],
                )
            ),
            "email_prefix": str(
                payload.get(
                    "email_prefix",
                    DEFAULTS["email_prefix"],
                )
            ),
            "password": DEFAULTS["password"],
        }

        if not 1 <= config["users"] <= 10_000:
            raise ValueError("Users must be between 1 and 10,000.")

        if not 1 <= config["events"] <= 100:
            raise ValueError("Events must be between 1 and 100.")

        if not 1 <= config["seats_per_event"] <= 100:
            raise ValueError(
                "Seats per event must be between 1 and 100."
            )

        if not 1 <= config["user_concurrency"] <= 2_000:
            raise ValueError(
                "User concurrency must be between 1 and 2,000."
            )

        if not 1 <= config["registration_concurrency"] <= 500:
            raise ValueError(
                "Registration concurrency must be between 1 and 500."
            )

        if config["think_time_min"] < 0:
            raise ValueError("Think time minimum cannot be negative.")

        if config["think_time_max"] < config["think_time_min"]:
            raise ValueError(
                "Think time maximum must be >= minimum."
            )

        if not 0 <= config["food_probability"] <= 1:
            raise ValueError(
                "Food probability must be between 0 and 1."
            )

        if not 0 <= config["payment_success_probability"] <= 1:
            raise ValueError(
                "Payment success probability must be between 0 and 1."
            )

        if config["scenario"] not in {
            "distributed",
            "hotspot",
            "random",
        }:
            raise ValueError("Invalid traffic scenario.")

        if not config["email_domain"]:
            raise ValueError("Email domain is required.")

        if not config["email_prefix"]:
            raise ValueError("Email prefix is required.")

        reset_state(config)
        STOP_EVENT.clear()

        RUN_THREAD = threading.Thread(
            target=run_test,
            args=(config,),
            name="ticketflow-real-user-load-test",
            daemon=True,
        )
        RUN_THREAD.start()

        return jsonify({"status": "started"})

    except (TypeError, ValueError) as exc:
        return jsonify({"error": str(exc)}), 400


@APP.post("/api/stop")
def stop_test():
    STOP_EVENT.set()

    with LOCK:
        if STATE["running"]:
            STATE["phase"] = "STOPPING"

    return jsonify({"status": "stop requested"})


@APP.get("/api/status")
def status():
    return jsonify(build_snapshot())


def report_file(key: str):
    with LOCK:
        filename = STATE[key]

    if not filename:
        return jsonify({"error": "Report is not available yet."}), 404

    return send_file(REPORT_DIR / filename)


@APP.get("/report/json")
def report_json():
    return report_file("report_json")


@APP.get("/report/csv")
def report_csv():
    return report_file("report_csv")


@APP.get("/report/html")
def report_html():
    return report_file("report_html")


if __name__ == "__main__":
    print("=" * 72)
    print("TicketFlow Load Lab — Real User Journey")
    print("=" * 72)
    print("Dashboard : http://127.0.0.1:5000")
    print(f"Reports   : {REPORT_DIR}")
    print()
    print("Install:")
    print("  python3 -m pip install flask requests")
    print()
    print("Use a dedicated test database.")
    print("=" * 72)

    APP.run(
        host="0.0.0.0",
        port=int(os.getenv("PORT", "5000")),
        threaded=True,
    )
