import argparse
import logging
import sys
from datetime import date

from etl.config import LOG_FORMAT, DEFAULT_START_DATE, DEFAULT_END_DATE
from etl.raw import run_raw_etl
from etl.core import run_core_etl
from etl.dimensions import run_dimension_etl
from etl.mart import run_mart_etl


LAYER_ORDER = {
    "raw": run_raw_etl,
    "dimensions": run_dimension_etl,
    "core": run_core_etl,
    "mart": run_mart_etl,
}


def main():
    parser = argparse.ArgumentParser(description="ETL Pipeline: Source -> Raw -> Core -> Mart")
    parser.add_argument(
        "--start", type=str, default=None,
        help=f"Start date (YYYY-MM-DD), default: {DEFAULT_START_DATE}",
    )
    parser.add_argument(
        "--end", type=str, default=None,
        help=f"End date (YYYY-MM-DD), default: {DEFAULT_END_DATE}",
    )
    parser.add_argument(
        "--debug", action="store_true",
        help="Enable DEBUG logging",
    )
    parser.add_argument(
        "--layers", type=str, default="raw,core,mart",
        help="Comma-separated list of layers to run (raw,dimensions,core,mart)",
    )

    args = parser.parse_args()

    level = logging.DEBUG if args.debug else logging.INFO
    logging.basicConfig(level=level, format=LOG_FORMAT)

    start_date = date.fromisoformat(args.start) if args.start else DEFAULT_START_DATE
    end_date = date.fromisoformat(args.end) if args.end else DEFAULT_END_DATE

    if start_date > end_date:
        logging.error("Start date (%s) > end date (%s)", start_date, end_date)
        sys.exit(1)

    requested_layers = [l.strip() for l in args.layers.split(",")]
    for layer in requested_layers:
        if layer not in LAYER_ORDER:
            logging.error("Unknown layer: %s (available: %s)", layer, ", ".join(LAYER_ORDER))
            sys.exit(1)

    layers_to_run = [l for l in LAYER_ORDER if l in requested_layers]
    if not layers_to_run:
        logging.error("No layers to run")
        sys.exit(1)

    results = {}
    for layer_name in layers_to_run:
        print(f"\n{'=' * 60}")
        print(f"Running layer: {layer_name}")
        print(f"{'=' * 60}")

        try:
            if layer_name in ("raw", "core", "mart"):
                result = LAYER_ORDER[layer_name](start_date, end_date)
            else:
                result = LAYER_ORDER[layer_name]()

            results[layer_name] = result
            print(f"  Status: OK")
            for key, val in result.items():
                print(f"  {key}: {val}")

        except Exception as e:
            logging.error("Layer %s failed: %s", layer_name, e)
            sys.exit(1)

    print(f"\n{'=' * 60}")
    print("ETL Pipeline completed successfully")
    print(f"{'=' * 60}")
    for layer_name, result in results.items():
        print(f"  {layer_name}: {result.get('rows_written', result.get('rows_loaded', result.get('route_card_rows', 'OK')))}")


if __name__ == "__main__":
    main()
