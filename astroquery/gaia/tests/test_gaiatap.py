# Licensed under a 3-clause BSD style license - see LICENSE.rst
"""
=============
Gaia TAP plus
=============

@author: Juan Carlos Segovia
@contact: juan.carlos.segovia@sciops.esa.int

European Space Astronomy Centre (ESAC)
European Space Agency (ESA)

Created on 30 jun. 2016


"""
from pathlib import Path
from unittest.mock import patch

import pytest
from astropy.table import Column, Table
from requests import HTTPError

from astroquery.gaia import conf
from astroquery.gaia.core import GaiaClass
from astroquery.utils.tap.conn.tests.DummyConnHandler import DummyConnHandler
from astroquery.utils.tap.conn.tests.DummyResponse import DummyResponse
import astropy.units as u
from astropy.coordinates.sky_coordinate import SkyCoord
import numpy as np
from astroquery.utils.tap.core import TapPlus


GAIA_QUERIER = GaiaClass(show_server_messages=False)
job_data = (Path(__file__).with_name("data") / "job_1.vot").read_text()

skycoord = SkyCoord(ra=19 * u.deg, dec=20 * u.deg, frame="icrs")


@pytest.fixture(scope="module")
def column_attrs():
    dtypes = {
        "alpha": np.float64,
        "delta": np.float64,
        "source_id": object,
        "table1_oid": np.int32
    }
    columns = {k: Column(name=k, description=k, dtype=v) for k, v in dtypes.items()}

    columns["source_id"].meta = {"_votable_string_dtype": "char"}
    return columns


@pytest.fixture(scope="module")
def mock_querier():
    conn_handler = DummyConnHandler()
    tapplus = TapPlus("http://test:1111/tap", connhandler=conn_handler)
    launch_response = DummyResponse(200)
    launch_response.set_data(method="POST", body=job_data)
    # The query contains decimals: default response is more robust.
    conn_handler.set_default_response(launch_response)
    return GaiaClass(tap_plus_conn_handler=conn_handler, datalink_handler=tapplus, show_server_messages=False)


@pytest.fixture(scope="module")
def mock_querier_async():
    conn_handler = DummyConnHandler()
    tapplus = TapPlus("http://test:1111/tap", connhandler=conn_handler)
    jobid = "12345"

    launch_response = DummyResponse(303)
    launch_response_headers = [["location", "http://test:1111/tap/async/" + jobid]]
    launch_response.set_data(method="POST", headers=launch_response_headers)
    conn_handler.set_default_response(launch_response)

    phase_response = DummyResponse(200)
    phase_response.set_data(method="GET", body="COMPLETED")
    conn_handler.set_response("async/" + jobid + "/phase", phase_response)

    results_response = DummyResponse(200)
    results_response.set_data(method="GET", body=job_data)
    conn_handler.set_response("async/" + jobid + "/results/result", results_response)

    return GaiaClass(tap_plus_conn_handler=conn_handler, datalink_handler=tapplus, show_server_messages=False)


def test_show_message():
    connHandler = DummyConnHandler()

    dummy_response = DummyResponse(200)

    message_text = "1653401204784D[type: -100,-1]=Gaia dev is under maintenance"

    dummy_response.set_data(method='GET', body=message_text)
    connHandler.set_default_response(dummy_response)

    # show_server_messages
    tableRequest = 'notification?action=GetNotifications'
    connHandler.set_response(tableRequest, dummy_response)

    tapplus = TapPlus("http://test:1111/tap", connhandler=connHandler)
    GaiaClass(tap_plus_conn_handler=connHandler, datalink_handler=tapplus, show_server_messages=True)


def test_query_object(column_attrs, mock_querier):
    with pytest.raises(ValueError) as err:
        mock_querier.query_object(skycoord)
    assert "Missing required argument: width" in err.value.args[0]

    width = 12 * u.deg

    with pytest.raises(ValueError) as err:
        mock_querier.query_object(skycoord, width=width)
    assert "Missing required argument: height" in err.value.args[0]

    table = mock_querier.query_object(skycoord, width=width, height=10 * u.deg)
    assert len(table) == 3
    for colname, attrs in column_attrs.items():
        assert table[colname].attrs_equal(attrs)
    # by radius
    table = mock_querier.query_object(skycoord, radius=1 * u.deg)
    assert len(table) == 3
    for colname, attrs in column_attrs.items():
        assert table[colname].attrs_equal(attrs)


def test_query_object_async(column_attrs, mock_querier_async):
    table = mock_querier_async.query_object_async(
        skycoord, width=12 * u.deg, height=10 * u.deg
    )
    assert len(table) == 3
    for colname, attrs in column_attrs.items():
        assert table[colname].attrs_equal(attrs)
    # by radius
    table = mock_querier_async.query_object_async(skycoord, radius=1 * u.deg)
    assert len(table) == 3
    for colname, attrs in column_attrs.items():
        assert table[colname].attrs_equal(attrs)


def test_cone_search_sync(column_attrs, mock_querier):
    job = mock_querier.cone_search(skycoord, radius=1 * u.deg)
    assert job.async_ is False
    assert job.get_phase() == "COMPLETED"
    assert job.failed is False
    # results
    results = job.get_results()
    assert len(results) == 3
    for colname, attrs in column_attrs.items():
        assert results[colname].attrs_equal(attrs)


def test_cone_search_async(column_attrs, mock_querier_async):
    radius = 1.0 * u.deg
    job = mock_querier_async.cone_search_async(skycoord, radius=radius)
    assert job.async_ is True
    assert job.get_phase() == "COMPLETED"
    assert job.failed is False
    # results
    results = job.get_results()
    assert len(results) == 3
    for colname, attrs in column_attrs.items():
        assert results[colname].attrs_equal(attrs)

    # Regression test for #2093 and #2099 - changing the MAIN_GAIA_TABLE
    # had no effect.
    # The preceding tests should have used the default value.
    assert 'gaiadr3.gaia_source' in job.parameters['query']
    with conf.set_temp("MAIN_GAIA_TABLE", "name_from_conf"):
        job = mock_querier_async.cone_search_async(skycoord, radius=radius)
        assert "name_from_conf" in job.parameters["query"]
        # Changing the value through the class should overrule conf.
        mock_querier_async.MAIN_GAIA_TABLE = "name_from_class"
        job = mock_querier_async.cone_search_async(skycoord, radius=radius)
        assert "name_from_class" in job.parameters["query"]


def test_load_data(monkeypatch, tmp_path):

    def load_data_monkeypatched(self, params_dict, output_file, verbose):
        assert params_dict == {
            "VALID_DATA": "true",
            "ID": "1,2,3,4",
            "FORMAT": "votable",
            "RETRIEVAL_TYPE": "epoch_photometry",
            "DATA_STRUCTURE": "INDIVIDUAL",
            "USE_ZIP_ALWAYS": "true"}
        assert output_file == str(tmp_path / "output_file")
        assert verbose is True

    monkeypatch.setattr(TapPlus, "load_data", load_data_monkeypatched)

    GAIA_QUERIER.load_data(
        ids="1,2,3,4",
        retrieval_type="epoch_photometry",
        valid_data=True,
        verbose=True,
        output_file=tmp_path / "output_file")


def test_get_datalinks(monkeypatch):

    def get_datalinks_monkeypatched(self, ids, verbose):
        return Table()

    # `GaiaClass` is a subclass of `TapPlus`, but it does not inherit
    # `get_datalinks()`, it replaces it with a call to the `get_datalinks()`
    # of its `__gaiadata`.
    monkeypatch.setattr(TapPlus, "get_datalinks", get_datalinks_monkeypatched)
    result = GAIA_QUERIER.get_datalinks(ids=["1", "2", "3", "4"], verbose=True)
    assert isinstance(result, Table)


def test_xmatch(mock_querier_async):
    # missing table A
    with pytest.raises(ValueError) as err:
        mock_querier_async.cross_match(
            full_qualified_table_name_b='schemaB.tableB',
            results_table_name='results',
        )
    assert "Table name A argument is mandatory" in err.value.args[0]
    # missing schema A
    with pytest.raises(ValueError) as err:
        mock_querier_async.cross_match(
            full_qualified_table_name_a='tableA',
            full_qualified_table_name_b='schemaB.tableB',
            results_table_name='results',
        )
    assert "Not found schema name in full qualified table A: 'tableA'" \
           in err.value.args[0]
    # missing table B
    with pytest.raises(ValueError) as err:
        mock_querier_async.cross_match(
            full_qualified_table_name_a='schemaA.tableA',
            results_table_name='results',
        )
    assert "Table name B argument is mandatory" in err.value.args[0]
    # missing schema B
    with pytest.raises(ValueError) as err:
        mock_querier_async.cross_match(
            full_qualified_table_name_a='schemaA.tableA',
            full_qualified_table_name_b='tableB',
            results_table_name='results',
        )
    assert "Not found schema name in full qualified table B: 'tableB'" \
           in err.value.args[0]
    # missing results table
    with pytest.raises(ValueError) as err:
        mock_querier_async.cross_match(
            full_qualified_table_name_a='schemaA.tableA',
            full_qualified_table_name_b='schemaB.tableB',
        )
    assert "Results table name argument is mandatory" in err.value.args[0]
    # wrong results table (with schema)
    with pytest.raises(ValueError) as err:
        mock_querier_async.cross_match(
            full_qualified_table_name_a='schemaA.tableA',
            full_qualified_table_name_b='schemaB.tableB',
            results_table_name='schema.results',
        )
    assert "Please, do not specify schema for 'results_table_name'" \
           in err.value.args[0]
    # radius < 0.1
    with pytest.raises(ValueError) as err:
        mock_querier_async.cross_match(
            full_qualified_table_name_a='schemaA.tableA',
            full_qualified_table_name_b='schemaB.tableB',
            results_table_name='results',
            radius=0.01,
        )
    assert "Invalid radius value. Found 0.01, valid range is: 0.1 to 10.0" \
           in err.value.args[0]
    # radius > 10.0
    with pytest.raises(ValueError) as err:
        mock_querier_async.cross_match(
            full_qualified_table_name_a='schemaA.tableA',
            full_qualified_table_name_b='schemaB.tableB',
            results_table_name='results',
            radius=10.1
        )
    assert "Invalid radius value. Found 10.1, valid range is: 0.1 to 10.0" \
           in err.value.args[0]
    job = mock_querier_async.cross_match(
        full_qualified_table_name_a='schemaA.tableA',
        full_qualified_table_name_b='schemaB.tableB',
        results_table_name='results',
    )
    assert job.async_ is True
    assert job.get_phase() == "COMPLETED"
    assert job.failed is False
    job = mock_querier_async.cross_match(
        full_qualified_table_name_a='schemaA.tableA',
        full_qualified_table_name_b='schemaB.tableB',
        results_table_name='results',
        background=True,
    )
    assert job.async_ is True
    assert job.get_phase() == "EXECUTING"
    assert job.failed is False


@patch.object(TapPlus, 'login')
def test_login(mock_login):
    conn_handler = DummyConnHandler()
    tapplus = TapPlus("http://test:1111/tap", connhandler=conn_handler)
    tap = GaiaClass(tap_plus_conn_handler=conn_handler, datalink_handler=tapplus, show_server_messages=False)
    tap.login(user="user", password="password")
    assert (mock_login.call_count == 2)
    mock_login.side_effect = HTTPError("Login error")
    tap.login(user="user", password="password")
    assert (mock_login.call_count == 3)


@patch.object(TapPlus, 'login_gui')
@patch.object(TapPlus, 'login')
def test_login_gui(mock_login_gui, mock_login):
    conn_handler = DummyConnHandler()
    tapplus = TapPlus("http://test:1111/tap", connhandler=conn_handler)
    tap = GaiaClass(tap_plus_conn_handler=conn_handler, datalink_handler=tapplus, show_server_messages=False)
    tap.login_gui()
    assert (mock_login_gui.call_count == 1)
    mock_login_gui.side_effect = HTTPError("Login error")
    tap.login(user="user", password="password")
    assert (mock_login.call_count == 1)


@patch.object(TapPlus, 'logout')
def test_logout(mock_logout):
    conn_handler = DummyConnHandler()
    tapplus = TapPlus("http://test:1111/tap", connhandler=conn_handler)
    tap = GaiaClass(tap_plus_conn_handler=conn_handler, datalink_handler=tapplus, show_server_messages=False)
    tap.logout()
    assert (mock_logout.call_count == 2)
    mock_logout.side_effect = HTTPError("Login error")
    tap.logout()
    assert (mock_logout.call_count == 3)
