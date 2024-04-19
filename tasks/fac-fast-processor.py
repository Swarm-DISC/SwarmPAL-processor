# ---
# jupyter:
#   jupytext:
#     formats: ipynb,py:percent
#     text_representation:
#       extension: .py
#       format_name: percent
#       format_version: '1.3'
#       jupytext_version: 1.16.1
#   kernelspec:
#     display_name: swarmpal-processor
#     language: python
#     name: swarmpal-processor
# ---

# %%
import argparse
import asyncio
import datetime as dt
import logging
import os
import re
import sched
import subprocess
import sys
import time

from dotenv import dotenv_values
from swarmpal.toolboxes.fac.presets import fac_single_sat
from swarmpal.utils.queries import last_available_time


# %%
def configure_logging():
    logger = logging.getLogger(__name__)
    logger.setLevel(logging.INFO)
    # Create a console handler and set its level to INFO
    handler = logging.StreamHandler()
    handler.setLevel(logging.INFO)
    # Create a formatter and set the format of log messages
    formatter = logging.Formatter("%(asctime)s - %(levelname)s:%(name)s:%(message)s")
    # Set the formatter for the handler
    handler.setFormatter(formatter)
    # Add the handler to the logger
    logger.addHandler(handler)
    return logger


LOGGER = configure_logging()
SCHEDULE = sched.scheduler(time.time, time.sleep)
WAIT_TIME = 900


# %%
def get_latest_evaluated(directory) -> "datetime":
    """Scan local directory to identify latest time in files"""

    if not os.path.exists(directory):
        os.makedirs(directory)

    dir_contents = os.listdir(directory)
    product_naming = r"SW_(FAST|OPER)_FAC(A|B|C)TMS_2F_(\d{8}T\d{6})_(\d{8}T\d{6})_.{4}\.(cdf|CDF)"
    matched_files = [s for s in dir_contents if re.search(product_naming, s)]
    matches = [re.match(product_naming, filename) for filename in matched_files]
    past_end_times = [match.group(4) for match in matches]
    past_end_times = [dt.datetime.strptime(s, "%Y%m%dT%H%M%S") for s in past_end_times]
    past_end_times.sort()
    try:
        # Add 1 second to convert naming scheme closed bound [a,b] to closed-open [a,b)
        return past_end_times[-1] + dt.timedelta(seconds=1)
    except IndexError:
        raise ValueError("No previous files found")


# %%
def job(swarm_spacecraft="A", starting_time=None, output_directory="outputs", wait_time=WAIT_TIME):
    collection_mag = f"SW_FAST_MAG{swarm_spacecraft}_LR_1B"
    # Check server for latest time in online products
    LOGGER.info(f"Checking product availability: {collection_mag}")
    t_latest_on_server = last_available_time(collection_mag).replace(microsecond=0)
    LOGGER.info(f"Latest availability for {collection_mag}: {t_latest_on_server}")
    # Check saved files for latest time evaluated
    try:
        t_latest_evaluated = get_latest_evaluated(output_directory)
    except ValueError:
        t_latest_evaluated = starting_time
    LOGGER.info(f"Latest processed time end point: {t_latest_evaluated}")
    # Run if there is new data available
    if t_latest_on_server != t_latest_evaluated:
        t_start = t_latest_evaluated
        t_end = t_latest_on_server
        LOGGER.info(f"Evaluating for time period: {t_start} to {t_end}")
        # Determine the name of the file to write (convert from closed-open [a,b) to closed-closed [a,b]
        t_startend_str = f'{t_start.strftime("%Y%m%dT%H%M%S")}_{(t_end - dt.timedelta(seconds=1)).strftime("%Y%m%dT%H%M%S")}'
        output_name = f"{output_directory}/SW_FAST_FAC{swarm_spacecraft}TMS_2F_{t_startend_str}_XXXX.cdf"
        fac_single_sat(spacecraft=f"Swarm-{swarm_spacecraft}", grade="FAST", time_start=t_start, time_end=t_end, output=output_name)
        # Prepare the next starting time to be the current end time
        t_latest_evaluated = t_end
        LOGGER.info(f"New data saved: {output_name}. Waiting to check again ({wait_time}s)")
    else:
        LOGGER.info(f"No new data available. Waiting to check again ({wait_time}s)")

    # Schedule next job run
    SCHEDULE.enter(wait_time, 1, job, (swarm_spacecraft, starting_time, output_directory, wait_time))


# %%
def start_job(spacecraft, output_directory):
    LOGGER.info(f"Beginning FAC FAST processor for Swarm {spacecraft}. Saving results to {output_directory}.")
    # Begin 3 days ago if output_directory is empty
    t0 = dt.datetime.now().date() - dt.timedelta(days=3)
    SCHEDULE.enter(0, 1, job, (spacecraft, t0, output_directory, WAIT_TIME))


# %%
def main():
    parser = argparse.ArgumentParser(
        prog='fac-fast-processor.py',
        description='...' # TODO
    )
    parser.add_argument(
        '-o', '--output-dir',
        action='store',
        default='outputs',
        help='Location, on local disk, for output files'
    )
    parser.add_argument(
        '-r', '--remote-dir',
        action='store',
        default='FAC/TMS',
        help='Location, on remote server, to sync output files to'
    )
    args = parser.parse_args()

    subprocess.Popen(['./inotifywait_rsync.sh', args.output_dir, args.remote_dir])

    for sat in ['A', 'B', 'C']:
        start_job(sat, os.path.join(args.output_dir, f'Sat_{sat}'))

    SCHEDULE.run()

if __name__ == "__main__":
    main()
