import copy
import datetime
import os
import signal
import subprocess
import time

import boto3
import pytest
import requests

from great_expectations.data_context.util import file_relative_path

USAGE_STATISTICS_QA_URL = (
    "https://qa.stats.greatexpectations.io/great_expectations/v1/usage_statistics"
)

logGroupName = "/great_expectations/usage_statistics/qa"


@pytest.fixture(scope="session")
def valid_usage_statistics_message():
    return {
        "event_payload": {
            "platform.system": "Darwin",
            "platform.release": "19.3.0",
            "version_info": "sys.version_info(major=3, minor=7, micro=4, releaselevel='final', serial=0)",
            "anonymized_datasources": [
                {
                    "anonymized_name": "f57d8a6edae4f321b833384801847498",
                    "parent_class": "SqlAlchemyDatasource",
                    "sqlalchemy_dialect": "postgresql",
                }
            ],
            "anonymized_stores": [
                {
                    "anonymized_name": "078eceafc1051edf98ae2f911484c7f7",
                    "parent_class": "ExpectationsStore",
                    "anonymized_store_backend": {
                        "parent_class": "TupleFilesystemStoreBackend"
                    },
                },
                {
                    "anonymized_name": "313cbd9858dd92f3fc2ef1c10ab9c7c8",
                    "parent_class": "ValidationsStore",
                    "anonymized_store_backend": {
                        "parent_class": "TupleFilesystemStoreBackend"
                    },
                },
                {
                    "anonymized_name": "2d487386aa7b39e00ed672739421473f",
                    "parent_class": "EvaluationParameterStore",
                    "anonymized_store_backend": {
                        "parent_class": "InMemoryStoreBackend"
                    },
                },
            ],
            "anonymized_validation_operators": [
                {
                    "anonymized_name": "99d14cc00b69317551690fb8a61aca94",
                    "parent_class": "ActionListValidationOperator",
                    "anonymized_action_list": [
                        {
                            "anonymized_name": "5a170e5b77c092cc6c9f5cf2b639459a",
                            "parent_class": "StoreValidationResultAction",
                        },
                        {
                            "anonymized_name": "0fffe1906a8f2a5625a5659a848c25a3",
                            "parent_class": "StoreEvaluationParametersAction",
                        },
                        {
                            "anonymized_name": "101c746ab7597e22b94d6e5f10b75916",
                            "parent_class": "UpdateDataDocsAction",
                        },
                    ],
                }
            ],
            "anonymized_data_docs_sites": [
                {
                    "parent_class": "SiteBuilder",
                    "anonymized_name": "eaf0cf17ad63abf1477f7c37ad192700",
                    "anonymized_store_backend": {
                        "parent_class": "TupleFilesystemStoreBackend"
                    },
                    "anonymized_site_index_builder": {
                        "parent_class": "DefaultSiteIndexBuilder",
                        "show_cta_footer": True,
                    },
                }
            ],
            "anonymized_expectation_suites": [
                {
                    "anonymized_name": "238e99998c7674e4ff26a9c529d43da4",
                    "expectation_count": 8,
                    "anonymized_expectation_type_counts": {
                        "expect_column_value_lengths_to_be_between": 1,
                        "expect_table_row_count_to_be_between": 1,
                        "expect_column_values_to_not_be_null": 2,
                        "expect_column_distinct_values_to_be_in_set": 1,
                        "expect_column_kl_divergence_to_be_less_than": 1,
                        "expect_table_column_count_to_equal": 1,
                        "expect_table_columns_to_match_ordered_list": 1,
                    },
                }
            ],
        },
        "event": "data_context.__init__",
        "success": True,
        "version": "1.0.0",
        "event_time": "2020-03-28T01:14:21.155Z",
        "data_context_id": "96c547fe-e809-4f2e-b122-0dc91bb7b3ad",
        "data_context_instance_id": "445a8ad1-2bd0-45ce-bb6b-d066afe996dd",
        "ge_version": "0.9.7+244.g56d67e51d.dirty",
    }


@pytest.fixture(scope="session")
def logstream(valid_usage_statistics_message):
    client = boto3.client("logs", region_name="us-east-1")
    # Warm up a logstream
    logStreamName = None
    message = copy.deepcopy(valid_usage_statistics_message)
    message["data_context_id"] = "00000000-0000-0000-0000-000000000000"
    res = requests.post(USAGE_STATISTICS_QA_URL, json=message)
    assert res.status_code == 201
    attempts = 0
    while attempts < 3:
        attempts += 1
        logStreams = client.describe_log_streams(
            logGroupName=logGroupName, orderBy="LastEventTime", descending=True, limit=2
        )
        lastEventTimestamp = logStreams["logStreams"][0].get("lastEventTimestamp")
        if lastEventTimestamp is not None:
            lastEvent = datetime.datetime.fromtimestamp(lastEventTimestamp / 1000)
            if (lastEvent - datetime.datetime.now()) < datetime.timedelta(seconds=30):
                logStreamName = logStreams["logStreams"][0]["logStreamName"]
                break
        time.sleep(2)
    if logStreamName is None:
        assert False, "Unable to warm up a log stream for integration testing."
    yield logStreamName


def test_send_malformed_data(valid_usage_statistics_message):
    # We should be able to successfully send a valid message, but find that
    # a malformed message is not accepted
    res = requests.post(USAGE_STATISTICS_QA_URL, json=valid_usage_statistics_message)
    assert res.status_code == 201
    invalid_usage_statistics_message = copy.deepcopy(valid_usage_statistics_message)
    del invalid_usage_statistics_message["data_context_id"]
    res = requests.post(USAGE_STATISTICS_QA_URL, json=invalid_usage_statistics_message)
    assert res.status_code == 400


def test_usage_statistics_transmission(logstream):
    logStreamName = logstream
    client = boto3.client("logs", region_name="us-east-1")
    pre_events = client.get_log_events(
        logGroupName=logGroupName, logStreamName=logStreamName, limit=100
    )
    assert len(pre_events) < 100, (
        "This test assumed small logstream sizes in the qa stream. Consider changing "
        "fetch limit."
    )

    usage_stats_url_env = dict(**os.environ)
    usage_stats_url_env["GE_USAGE_STATISTICS_URL"] = USAGE_STATISTICS_QA_URL
    p = subprocess.Popen(
        [
            "python",
            file_relative_path(
                __file__, "./instantiate_context_with_usage_statistics.py"
            ),
            "0",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=usage_stats_url_env,
    )
    outs, errs = p.communicate()
    outs = str(outs)
    errs = str(errs)
    assert "INFO" not in outs
    assert "Done constructing a DataContext" in outs
    assert "Ending a long nap" in outs
    assert "KeyboardInterrupt" not in errs

    # Wait a bit for the log events to post
    time.sleep(10)
    post_events = client.get_log_events(
        logGroupName=logGroupName, logStreamName=logStreamName, limit=100
    )
    assert len(pre_events["events"]) + 4 == len(post_events["events"])


def test_send_completes_on_kill(logstream):
    logStreamName = logstream
    client = boto3.client("logs", region_name="us-east-1")
    pre_events = client.get_log_events(
        logGroupName=logGroupName, logStreamName=logStreamName, limit=100
    )
    """Test that having usage statistics enabled does not negatively impact kill signals or cause loss of queued usage statistics. """
    # Execute process that initializes data context
    acceptable_startup_time = 6
    acceptable_shutdown_time = 1
    nap_time = 30
    start = datetime.datetime.now()
    usage_stats_url_env = dict(**os.environ)
    usage_stats_url_env["GE_USAGE_STATISTICS_URL"] = USAGE_STATISTICS_QA_URL
    # Instruct the process to wait for 30 seconds after initializing before completing.
    p = subprocess.Popen(
        [
            "python",
            file_relative_path(
                __file__, "./instantiate_context_with_usage_statistics.py"
            ),
            str(nap_time),
            "False",
            "True",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=usage_stats_url_env,
    )

    time.sleep(acceptable_startup_time)

    # Send a signal to kill
    p.send_signal(signal.SIGINT)
    outs, errs = p.communicate()
    end = datetime.datetime.now()

    # Ensure that the process shut down earlier than it would have
    assert (
        datetime.timedelta(seconds=acceptable_startup_time)
        < (end - start)
        < datetime.timedelta(seconds=acceptable_startup_time + acceptable_shutdown_time)
    )

    outs = str(outs)
    errs = str(errs)
    assert "INFO" not in outs
    assert "Done constructing a DataContext" in outs
    assert "Ending a long nap" not in outs
    assert "KeyboardInterrupt" in errs
    time.sleep(10)
    post_events = client.get_log_events(
        logGroupName=logGroupName, logStreamName=logStreamName, limit=100
    )
    assert len(pre_events["events"]) + 4 == len(post_events["events"])


def test_graceful_failure_with_no_internet():
    """Test that having usage statistics enabled does not negatively impact kill signals or cause loss of queued usage statistics. """

    # Execute process that initializes data context
    # NOTE - JPC - 20200227 - this is crazy long (not because of logging I think, but worth revisiting)
    acceptable_startup_time = 6
    acceptable_shutdown_time = 1
    nap_time = 0
    start = datetime.datetime.now()
    # Instruct the process to wait for 30 seconds after initializing before completing.
    p = subprocess.Popen(
        [
            "python",
            file_relative_path(
                __file__, "./instantiate_context_with_usage_statistics.py"
            ),
            str(nap_time),
            "True",
            "True",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    outs, errs = p.communicate()
    end = datetime.datetime.now()
    # We didn't wait or send a signal, so just check that times were reasonable
    assert (end - start) < datetime.timedelta(
        seconds=acceptable_startup_time + acceptable_shutdown_time
    )
    outs = str(outs)
    errs = str(errs)
    assert "INFO" not in outs
    assert "Done constructing a DataContext" in outs
    assert "Ending a long nap" in outs
