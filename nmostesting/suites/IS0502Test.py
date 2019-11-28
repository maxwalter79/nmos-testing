# Copyright 2018 British Broadcasting Corporation
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import time
import uuid
import re
from requests.compat import json

from ..GenericTest import GenericTest
from ..IS05Utils import IS05Utils
from .. import Config as CONFIG

NODE_API_KEY = "node"
CONN_API_KEY = "connection"


class IS0502Test(GenericTest):
    """
    Runs Tests covering both IS-04 and IS-05
    """
    def __init__(self, apis):
        # Don't auto-test /transportfile as it is permitted to generate a 404 when master_enable is false
        omit_paths = [
            "/single/senders/{senderId}/transportfile"
        ]
        GenericTest.__init__(self, apis, omit_paths)
        self.node_url = self.apis[NODE_API_KEY]["url"]
        self.connection_url = self.apis[CONN_API_KEY]["url"]
        self.is05_resources = {"senders": [], "receivers": [], "_requested": [], "transport_types": {},
                               "transport_files": {}}
        self.is04_resources = {"senders": [], "receivers": [], "_requested": [], "sources": [], "flows": []}
        self.is05_utils = IS05Utils(self.connection_url)

    def get_is04_resources(self, resource_type):
        """Retrieve all Senders or Receivers from a Node API, keeping hold of the returned objects"""
        assert(resource_type in ["senders", "receivers", "sources", "flows"])

        # Prevent this being executed twice in one test run
        if resource_type in self.is04_resources["_requested"]:
            return True, ""

        valid, resources = self.do_request("GET", self.node_url + resource_type)
        if not valid:
            return False, "Node API did not respond as expected: {}".format(resources)

        try:
            for resource in resources.json():
                self.is04_resources[resource_type].append(resource)
            self.is04_resources["_requested"].append(resource_type)
        except json.JSONDecodeError:
            return False, "Non-JSON response returned from Node API"

        return True, ""

    def refresh_is04_resources(self, resource_type):
        """Force a re-retrieval of the IS-04 Senders or Receivers, bypassing the cache"""
        if resource_type in self.is04_resources["_requested"]:
            self.is04_resources["_requested"].remove(resource_type)
            self.is04_resources[resource_type] = []

        return self.get_is04_resources(resource_type)

    def get_is05_resources(self, resource_type):
        """Retrieve all Senders or Receivers from a Connection API, keeping hold of the returned IDs"""
        assert(resource_type in ["senders", "receivers"])

        # Prevent this being executed twice in one test run
        if resource_type in self.is05_resources["_requested"]:
            return True, ""

        valid, resources = self.do_request("GET", self.connection_url + "single/" + resource_type)
        if not valid:
            return False, "Connection API did not respond as expected: {}".format(resources)

        try:
            for resource in resources.json():
                resource_id = resource.rstrip("/")
                self.is05_resources[resource_type].append(resource_id)
                if self.is05_utils.compare_api_version(self.apis[CONN_API_KEY]["version"], "v1.1") >= 0:
                    transport_type = self.is05_utils.get_transporttype(resource_id, resource_type.rstrip("s"))
                    self.is05_resources["transport_types"][resource_id] = transport_type
                else:
                    self.is05_resources["transport_types"][resource_id] = "urn:x-nmos:transport:rtp"
                if resource_type == "senders":
                    transport_file = self.is05_utils.get_transportfile(resource_id)
                    self.is05_resources["transport_files"][resource_id] = transport_file
            self.is05_resources["_requested"].append(resource_type)
        except json.JSONDecodeError:
            return False, "Non-JSON response returned from Node API"

        return True, ""

    def check_is04_in_is05(self, resource_type):
        """Check that each Sender or Receiver found via IS-04 has a matching entry in IS-05"""
        assert(resource_type in ["senders", "receivers"])

        result = True
        for is04_resource in self.is04_resources[resource_type]:
            valid_transports = self.is05_utils.get_valid_transports(self.apis[CONN_API_KEY]["version"])
            if is04_resource["transport"] in valid_transports:
                if is04_resource["id"] not in self.is05_resources[resource_type]:
                    result = False

        return result

    def check_is05_in_is04(self, resource_type):
        """Check that each Sender or Receiver found via IS-05 has a matching entry in IS-04"""
        assert(resource_type in ["senders", "receivers"])

        result = True
        for is05_resource in self.is05_resources[resource_type]:
            is05_res_ok = False
            for is04_resource in self.is04_resources[resource_type]:
                if is04_resource["id"] == is05_resource:
                    is05_res_ok = True
                    break
            result = is05_res_ok
            if not result:
                break

        return result

    def activate_check_version(self, resource_type, resource_list):
        try:
            for is05_resource in resource_list:
                found_04_resource = False
                for is04_resource in self.is04_resources[resource_type]:
                    if is04_resource["id"] == is05_resource:
                        found_04_resource = True
                        current_ver = is04_resource["version"]
                        transport_type = self.is05_resources["transport_types"][is05_resource]

                        method = self.is05_utils.check_perform_immediate_activation
                        valid, response = self.is05_utils.check_activation(resource_type.rstrip("s"), is05_resource,
                                                                           method, transport_type)
                        if not valid:
                            return False, response

                        time.sleep(CONFIG.API_PROCESSING_TIMEOUT)

                        valid, response = self.do_request("GET", self.node_url + resource_type + "/" + is05_resource)
                        if not valid:
                            return False, "Node API did not respond as expected: {}".format(response)

                        new_ver = response.json()["version"]

                        if self.is05_utils.compare_resource_version(new_ver, current_ver) != 1:
                            return False, "IS-04 resource version did not change when {} {} was activated" \
                                          .format(resource_type.rstrip("s").capitalize(), is05_resource)

                if not found_04_resource:
                    return False, "Unable to find an IS-04 resource with ID {}".format(is05_resource)

        except json.JSONDecodeError:
            return False, "Non-JSON response returned from Node API"
        except KeyError:
            return False, "Version attribute was not found in IS-04 resource"

        return True, ""

    def activate_check_parked(self, resource_type, resource_list):
        for is05_resource in resource_list:
            valid, response = self.is05_utils.park_resource(resource_type, is05_resource)
            if not valid:
                return False, response

        time.sleep(CONFIG.API_PROCESSING_TIMEOUT)

        valid, result = self.refresh_is04_resources(resource_type)
        if not valid:
            return False, result

        try:
            api = self.apis[NODE_API_KEY]
            for is05_resource in self.is05_resources[resource_type]:
                found_04_resource = False
                for is04_resource in self.is04_resources[resource_type]:
                    if is04_resource["id"] == is05_resource:
                        found_04_resource = True
                        subscription = is04_resource["subscription"]

                        # Only IS-04 v1.2+ has an 'active' subscription key
                        if self.is05_utils.compare_api_version(api["version"], "v1.2") >= 0:
                            if subscription["active"] is not False:
                                return False, "IS-04 {} {} was not marked as inactive when IS-05 master_enable set to" \
                                              " false".format(resource_type.rstrip("s").capitalize(), is05_resource)

                        id_key = "sender_id"
                        if resource_type == "senders":
                            id_key = "receiver_id"
                        if subscription[id_key] is not None:
                            return False, "IS-04 {} {} still indicates a subscribed '{}' when parked".format(
                                          resource_type.rstrip("s").capitalize(), is05_resource, id_key)

                if not found_04_resource:
                    return False, "Unable to find an IS-04 resource with ID {}".format(is05_resource)

        except KeyError:
            return False, "Subscription attribute was not found in IS-04 resource"

        return True, ""

    def activate_check_subscribed(self, resource_type, resource_list, nmos=True, multicast=True):
        sub_ids = {}
        for is05_resource in resource_list:
            if self.is05_resources["transport_types"][is05_resource] != "urn:x-nmos:transport:rtp":
                continue

            if (resource_type == "receivers" and nmos) or \
               (resource_type == "senders" and nmos and not multicast):
                sub_id = str(uuid.uuid4())
            else:
                sub_id = None
            sub_ids[is05_resource] = sub_id
            valid, response = self.is05_utils.subscribe_resource(resource_type, is05_resource, sub_id, multicast)
            if not valid:
                return False, response

        time.sleep(CONFIG.API_PROCESSING_TIMEOUT)

        valid, result = self.refresh_is04_resources(resource_type)
        if not valid:
            return False, result

        try:
            api = self.apis[NODE_API_KEY]
            for is05_resource in self.is05_resources[resource_type]:
                if self.is05_resources["transport_types"][is05_resource] != "urn:x-nmos:transport:rtp":
                    continue

                found_04_resource = False
                for is04_resource in self.is04_resources[resource_type]:
                    if is04_resource["id"] == is05_resource:
                        found_04_resource = True
                        subscription = is04_resource["subscription"]

                        # Only IS-04 v1.2+ has an 'active' subscription key
                        if self.is05_utils.compare_api_version(api["version"], "v1.2") >= 0:
                            if subscription["active"] is not True:
                                return False, "IS-04 {} {} was not marked as active when IS-05 master_enable set to" \
                                              " true".format(resource_type.rstrip("s").capitalize(), is05_resource)

                        id_key = "sender_id"
                        if resource_type == "senders":
                            id_key = "receiver_id"
                        if subscription[id_key] != sub_ids[is05_resource]:
                            return False, "IS-04 {} {} indicates subscription to '{}' rather than '{}'".format(
                                          resource_type.rstrip("s").capitalize(), is05_resource, subscription[id_key],
                                          sub_ids[is05_resource])

                if not found_04_resource:
                    return False, "Unable to find an IS-04 resource with ID {}".format(is05_resource)

        except KeyError:
            return False, "Subscription attribute was not found in IS-04 resource"

        return True, ""

    def test_01(self, test):
        """Check that version 1.2 or greater of the Node API is available"""

        api = self.apis[NODE_API_KEY]
        if self.is05_utils.compare_api_version(api["version"], "v1.2") >= 0:
            valid, result = self.do_request("GET", self.node_url)
            if valid:
                return test.PASS()
            else:
                return test.FAIL("Node API did not respond as expected: {}".format(result))
        else:
            return test.FAIL("Node API must be running v1.2 or greater")

    def test_02(self, test):
        """At least one Device is showing an IS-05 control advertisement matching the API under test"""

        valid, devices = self.do_request("GET", self.node_url + "devices")
        if not valid:
            return test.FAIL("Node API did not respond as expected: {}".format(devices))

        is05_devices = []
        found_api_match = False
        try:
            device_type = "urn:x-nmos:control:sr-ctrl/" + self.apis[CONN_API_KEY]["version"]
            for device in devices.json():
                controls = device["controls"]
                for control in controls:
                    if control["type"] == device_type:
                        is05_devices.append(control["href"])
                        if self.is05_utils.compare_urls(self.connection_url, control["href"]):
                            found_api_match = True
        except json.JSONDecodeError:
            return test.FAIL("Non-JSON response returned from Node API")
        except KeyError:
            return test.FAIL("One or more Devices were missing the 'controls' attribute")

        if len(is05_devices) > 0 and found_api_match:
            return test.PASS()
        elif len(is05_devices) > 0:
            return test.FAIL("Found one or more Device controls, but no href matched the Connection API under test")
        else:
            return test.FAIL("Unable to find any Devices which expose the control type '{}'".format(device_type))

    def test_03(self, test):
        """Receivers shown in Connection API matches those shown in Node API"""

        valid, result = self.get_is04_resources("receivers")
        if not valid:
            return test.FAIL(result)
        valid, result = self.get_is05_resources("receivers")
        if not valid:
            return test.FAIL(result)

        if not self.check_is04_in_is05("receivers"):
            return test.FAIL("Unable to find all Receivers from IS-04 in IS-05")

        if not self.check_is05_in_is04("receivers"):
            return test.FAIL("Unable to find all Receivers from IS-05 in IS-04")

        return test.PASS()

    def test_04(self, test):
        """Senders shown in Connection API matches those shown in Node API"""

        valid, result = self.get_is04_resources("senders")
        if not valid:
            return test.FAIL(result)
        valid, result = self.get_is05_resources("senders")
        if not valid:
            return test.FAIL(result)

        if not self.check_is04_in_is05("senders"):
            return test.FAIL("Unable to find all Senders from IS-04 in IS-05")

        if not self.check_is05_in_is04("senders"):
            return test.FAIL("Unable to find all Senders from IS-05 in IS-04")

        return test.PASS()

    def test_05(self, test):
        """Activation of a receiver increments the version timestamp"""

        resource_type = "receivers"

        valid, result = self.refresh_is04_resources(resource_type)
        if not valid:
            return test.FAIL(result)
        valid, result = self.get_is05_resources(resource_type)
        if not valid:
            return test.FAIL(result)

        if len(self.is05_resources[resource_type]) == 0:
            return test.UNCLEAR("Could not find any IS-05 Receivers to test")

        resource_subset = self.is05_utils.sampled_list(self.is05_resources[resource_type])
        valid, response = self.activate_check_version(resource_type, resource_subset)
        if not valid:
            return test.FAIL(response)
        else:
            return test.PASS()

    def test_06(self, test):
        """Activation of a sender increments the version timestamp"""

        resource_type = "senders"

        valid, result = self.refresh_is04_resources(resource_type)
        if not valid:
            return test.FAIL(result)
        valid, result = self.get_is05_resources(resource_type)
        if not valid:
            return test.FAIL(result)

        if len(self.is05_resources[resource_type]) == 0:
            return test.UNCLEAR("Could not find any IS-05 Senders to test")

        resource_subset = self.is05_utils.sampled_list(self.is05_resources[resource_type])
        valid, response = self.activate_check_version(resource_type, resource_subset)
        if not valid:
            return test.FAIL(response)
        else:
            return test.PASS()

    def test_07(self, test):
        """Activation of a receiver from an NMOS sender updates the IS-04 subscription"""

        resource_type = "receivers"

        valid, result = self.get_is04_resources(resource_type)
        if not valid:
            return test.FAIL(result)
        valid, result = self.get_is05_resources(resource_type)
        if not valid:
            return test.FAIL(result)

        if len(self.is05_resources[resource_type]) == 0:
            return test.UNCLEAR("Could not find any IS-05 Receivers to test")

        resource_subset = self.is05_utils.sampled_list(self.is05_resources[resource_type])
        valid, response = self.activate_check_parked(resource_type, resource_subset)
        if not valid:
            return test.FAIL(response)

        valid, response = self.activate_check_subscribed(resource_type, resource_subset, nmos=True)
        if not valid:
            return test.FAIL(response)
        else:
            return test.PASS()

    def test_08(self, test):
        """Activation of a receiver from a non-NMOS sender updates the IS-04 subscription"""

        resource_type = "receivers"

        valid, result = self.get_is04_resources(resource_type)
        if not valid:
            return test.FAIL(result)
        valid, result = self.get_is05_resources(resource_type)
        if not valid:
            return test.FAIL(result)

        if len(self.is05_resources[resource_type]) == 0:
            return test.UNCLEAR("Could not find any IS-05 Receivers to test")

        resource_subset = self.is05_utils.sampled_list(self.is05_resources[resource_type])
        valid, response = self.activate_check_parked(resource_type, resource_subset)
        if not valid:
            return test.FAIL(response)

        valid, response = self.activate_check_subscribed(resource_type, resource_subset, nmos=False)
        if not valid:
            return test.FAIL(response)
        else:
            return test.PASS()

    def test_09(self, test):
        """Activation of a sender to a multicast address updates the IS-04 subscription"""

        api = self.apis[NODE_API_KEY]
        if self.is05_utils.compare_api_version(api["version"], "v1.2") < 0:
            return test.NA("IS-04 v1.1 and earlier Senders do not have a subscription object")

        resource_type = "senders"

        valid, result = self.get_is04_resources(resource_type)
        if not valid:
            return test.FAIL(result)
        valid, result = self.get_is05_resources(resource_type)
        if not valid:
            return test.FAIL(result)

        if len(self.is05_resources[resource_type]) == 0:
            return test.UNCLEAR("Could not find any IS-05 Senders to test")

        resource_subset = self.is05_utils.sampled_list(self.is05_resources[resource_type])
        valid, response = self.activate_check_parked(resource_type, resource_subset)
        if not valid:
            return test.FAIL(response)

        valid, response = self.activate_check_subscribed(resource_type, resource_subset, nmos=True)
        if not valid:
            return test.FAIL(response)
        else:
            return test.PASS()

    def test_10(self, test):
        """Activation of a sender to a unicast NMOS receiver updates the IS-04 subscription"""

        api = self.apis[NODE_API_KEY]
        if self.is05_utils.compare_api_version(api["version"], "v1.2") < 0:
            return test.NA("IS-04 v1.1 and earlier Senders do not have a subscription object")

        resource_type = "senders"

        valid, result = self.get_is04_resources(resource_type)
        if not valid:
            return test.FAIL(result)
        valid, result = self.get_is05_resources(resource_type)
        if not valid:
            return test.FAIL(result)

        if len(self.is05_resources[resource_type]) == 0:
            return test.UNCLEAR("Could not find any IS-05 Senders to test")

        resource_subset = self.is05_utils.sampled_list(self.is05_resources[resource_type])
        valid, response = self.activate_check_parked(resource_type, resource_subset)
        if not valid:
            return test.FAIL(response)

        valid, response = self.activate_check_subscribed(resource_type, resource_subset, nmos=True, multicast=False)
        if not valid:
            return test.FAIL(response)
        else:
            return test.PASS()

    def test_11(self, test):
        """Activation of a sender to a unicast non-NMOS receiver updates the IS-04 subscription"""

        api = self.apis[NODE_API_KEY]
        if self.is05_utils.compare_api_version(api["version"], "v1.2") < 0:
            return test.NA("IS-04 v1.1 and earlier Senders do not have a subscription object")

        resource_type = "senders"

        valid, result = self.get_is04_resources(resource_type)
        if not valid:
            return test.FAIL(result)
        valid, result = self.get_is05_resources(resource_type)
        if not valid:
            return test.FAIL(result)

        if len(self.is05_resources[resource_type]) == 0:
            return test.UNCLEAR("Could not find any IS-05 Senders to test")

        resource_subset = self.is05_utils.sampled_list(self.is05_resources[resource_type])
        valid, response = self.activate_check_parked(resource_type, resource_subset)
        if not valid:
            return test.FAIL(response)

        valid, response = self.activate_check_subscribed(resource_type, resource_subset, nmos=False, multicast=False)
        if not valid:
            return test.FAIL(response)
        else:
            return test.PASS()

    def test_12(self, test):
        """IS-04 interface bindings array matches length of IS-05 transport_params array"""

        for resource_type in ["senders", "receivers"]:
            valid, result = self.get_is04_resources(resource_type)
            if not valid:
                return test.FAIL(result)

        try:
            for resource_type in ["senders", "receivers"]:
                for resource in self.is04_resources[resource_type]:
                    valid_transports = self.is05_utils.get_valid_transports(self.apis[CONN_API_KEY]["version"])
                    if resource["transport"] not in valid_transports:
                        continue

                    bindings_length = len(resource["interface_bindings"])
                    url_path = self.connection_url + "single/" + resource_type + "/" + resource["id"] + "/active"
                    valid, result = self.do_request("GET", url_path)
                    if not valid:
                        return test.FAIL("Connection API returned unexpected result "
                                         "for {} '{}'".format(resource_type.capitalize(), resource["id"]))

                    trans_params_length = len(result.json()["transport_params"])
                    if trans_params_length != bindings_length:
                        return test.FAIL("Array length mismatch for Sender/Receiver ID '{}'".format(resource["id"]))

        except json.JSONDecodeError:
            return test.FAIL("Non-JSON response returned from Connection API")
        except KeyError as ex:
            return test.FAIL("Expected attribute not found in IS-04 Sender/Receiver "
                             "or IS-05 active resource: {}".format(ex))

        return test.PASS()

    def test_13(self, test):
        """IS-04 manifest_href matches IS-05 transportfile"""

        valid, result = self.get_is04_resources("senders")
        if not valid:
            return test.FAIL(result)

        try:
            for resource in self.is04_resources["senders"]:
                valid_transports = self.is05_utils.get_valid_transports(self.apis[CONN_API_KEY]["version"])
                if resource["transport"] not in valid_transports:
                    continue

                is04_transport_file = None
                is05_transport_file = None
                if resource["manifest_href"] is not None and resource["manifest_href"] != "":
                    valid, result = self.do_request("GET", resource["manifest_href"])
                    if valid and result.status_code != 404:
                        is04_transport_file = result.text
                url_path = self.connection_url + "single/senders/" + resource["id"] + "/transportfile"
                valid, result = self.do_request("GET", url_path)
                if valid and result.status_code != 404:
                    is05_transport_file = result.text

                if is04_transport_file != is05_transport_file:
                    return test.FAIL("Transport file contents for Sender '{}' do not match "
                                     "between IS-04 and IS-05".format(resource["id"]))

        except KeyError as ex:
            return test.FAIL("Expected attribute not found in IS-04 Sender: {}".format(ex))

        return test.PASS()

    def test_14(self, test):
        """IS-05 transportfile rtpmap parameters match IS-04 Source and Flow"""

        for resource_type in ["senders", "flows", "sources"]:
            valid, result = self.get_is04_resources(resource_type)
            if not valid:
                return test.FAIL(result)

        valid, result = self.get_is05_resources("senders")
        if not valid:
            return test.FAIL(result)

        if len(self.is04_resources["senders"]) == 0:
            return test.UNCLEAR("Could not find any IS-05 Senders to test")

        flow_map = {flow["id"]: flow for flow in self.is04_resources["flows"]}
        source_map = {source["id"]: source for source in self.is04_resources["sources"]}

        try:
            for resource in self.is04_resources["senders"]:
                if not resource["transport"].startswith("urn:x-nmos:transport:rtp"):
                    continue
                if resource["flow_id"] is None:
                    continue

                flow = flow_map[resource["flow_id"]]
                source = source_map[flow["source_id"]]

                is05_transport_file = self.is05_resources["transport_files"][resource["id"]]
                if is05_transport_file is None:
                    return test.FAIL("Unable to download transportfile for Sender {}".format(resource["id"]))

                payload_type = self.rtp_ptype(is05_transport_file)
                if not payload_type:
                    return test.FAIL("Unable to locate payload type from rtpmap in SDP file for Sender {}"
                                     .format(resource["id"]))

                for sdp_line in is05_transport_file.split("\n"):
                    sdp_line = sdp_line.replace("\r", "")
                    if sdp_line.startswith(r"a=rtpmap:"):
                        # Perform a coarse check first
                        rtpmap = re.search(r"^a=rtpmap:\d+ {}/.+$".format(flow["media_type"].split("/")[1]),
                                           sdp_line)
                        if not rtpmap:
                            return test.FAIL(r"a=rtpmap does not match Flow media type {} for Sender {}"
                                             .format(flow["media_type"], resource["id"]))

                        if source["format"] == "urn:x-nmos:format:video":
                            rtpmap = re.search(r"^a=rtpmap:\d+ {}/90000$".format(flow["media_type"].split("/")[1]),
                                               sdp_line)
                            if not rtpmap:
                                return test.FAIL("a=rtpmap clock rate does not match expected rate for Flow media "
                                                 "type {} and Sender {}".format(flow["media_type"], resource["id"]))
                        elif source["format"] == "urn:x-nmos:format:audio":
                            rtpmap = re.search(r"^a=rtpmap:\d+ L(\d+)\/(\d+)(?:\/(\d+))?$", sdp_line)
                            if re.search(r"^audio\/L\d+$", flow["media_type"]):
                                if not rtpmap:
                                    return test.FAIL("a=rtpmap does not match pattern expected for Flow media type {} "
                                                     "for Sender {}".format(flow["media_type"], resource["id"]))
                                bit_depth = int(rtpmap.group(1))
                                sample_rate = int(rtpmap.group(2))
                                channels = int(rtpmap.group(3)) if rtpmap.group(3) is not None else 1
                                if len(source["channels"]) != channels:
                                    return test.FAIL("Number of channels for Sender {} does not match its Source {}"
                                                     .format(resource["id"], source["id"]))
                                if flow["bit_depth"] != bit_depth:
                                    return test.FAIL("Bit depth for Sender {} does not match its Flow {}"
                                                     .format(resource["id"], flow["id"]))
                                if flow["sample_rate"]["numerator"] != sample_rate:
                                    return test.FAIL("Sample rate for Sender {} does not match its Flow {}"
                                                     .format(resource["id"], flow["id"]))
                                if flow["media_type"] != "audio/L{}".format(bit_depth):
                                    return test.FAIL("Mismatch between bit depth and media_type for Flow {}"
                                                     .format(flow["id"]))
                            elif rtpmap:
                                return test.FAIL("a=rtpmap specifies a different media_type to the Flow for Sender {}"
                                                 .format(resource["id"]))
                        elif source["format"] == "urn:x-nmos:format:data":
                            rtpmap = re.search(r"^a=rtpmap:\d+ smpte291/90000$", sdp_line)
                            if flow["media_type"] == "video/smpte291" and not rtpmap:
                                return test.FAIL("a=rtpmap clock rate does not match expected rate for Flow media "
                                                 "type {} and Sender {}".format(flow["media_type"], resource["id"]))
                            elif rtpmap:
                                return test.FAIL("a=rtpmap specifies a different media_type to the Flow for Sender {}"
                                                 .format(resource["id"]))
                        elif source["format"] == "urn:x-nmos:format:mux":
                            rtpmap = re.search(r"^a=rtpmap:\d+ SMPTE2022-6/27000000$", sdp_line)
                            if flow["media_type"] == "video/SMPTE2022-6" and not rtpmap:
                                return test.FAIL("a=rtpmap clock rate does not match expected rate for Flow media "
                                                 "type {} and Sender {}".format(flow["media_type"], resource["id"]))
                            elif rtpmap:
                                return test.FAIL("a=rtpmap specifies a different media_type to the Flow for Sender {}"
                                                 .format(resource["id"]))
        except KeyError as ex:
            return test.FAIL("Expected attribute not found in IS-04 resource: {}".format(ex))

        return test.PASS()

    def test_15(self, test):
        """IS-05 transportfile fmtp parameters match IS-04 Source and Flow"""

        for resource_type in ["senders", "flows", "sources"]:
            valid, result = self.get_is04_resources(resource_type)
            if not valid:
                return test.FAIL(result)

        valid, result = self.get_is05_resources("senders")
        if not valid:
            return test.FAIL(result)

        if len(self.is04_resources["senders"]) == 0:
            return test.UNCLEAR("Could not find any IS-05 Senders to test")

        flow_map = {flow["id"]: flow for flow in self.is04_resources["flows"]}
        source_map = {source["id"]: source for source in self.is04_resources["sources"]}

        try:
            for resource in self.is04_resources["senders"]:
                if not resource["transport"].startswith("urn:x-nmos:transport:rtp"):
                    continue
                if resource["flow_id"] is None:
                    continue

                flow = flow_map[resource["flow_id"]]
                source = source_map[flow["source_id"]]

                is05_transport_file = self.is05_resources["transport_files"][resource["id"]]
                if is05_transport_file is None:
                    return test.FAIL("Unable to download transportfile for Sender {}".format(resource["id"]))

                payload_type = self.rtp_ptype(is05_transport_file)
                if not payload_type:
                    return test.FAIL("Unable to locate payload type from rtpmap in SDP file for Sender {}"
                                     .format(resource["id"]))

                for sdp_line in is05_transport_file.split("\n"):
                    sdp_line = sdp_line.replace("\r", "")
                    fmtp = re.search(r"^a=fmtp:{} (.+)$".format(payload_type), sdp_line)
                    if fmtp and source["format"] == "urn:x-nmos:format:video":
                        for param in fmtp.group(1).split(";"):
                            param_components = param.strip().split("=")
                            if param_components[0] == "sampling":  # ref: RFC4175 and ST.2110-20
                                sampling_format = param_components[1].split("-")
                                components = sampling_format[0]
                                if components in ["YCbCr", "ICtCp", "RGB"]:
                                    if len(flow["components"]) != 3:
                                        return test.FAIL("Video Flow {} components do not match those found in SDP for "
                                                         "Sender {}", flow["id"], resource["id"])
                                    if len(sampling_format) > 1:
                                        sampling = sampling_format[1]
                                    else:
                                        sampling = None
                                    for component in flow["components"]:
                                        if component["name"] not in components:
                                            return test.FAIL("Video Flow component {} does not match the SDP sampling "
                                                             "for Sender {}".format(component["name"], resource["id"]))
                                        sampling_error = False
                                        if sampling == "4:4:4" or sampling is None or component["name"] in ["Y", "I"]:
                                            if component["width"] != flow["frame_width"] or \
                                                    component["height"] != flow["frame_height"]:
                                                sampling_error = True
                                        elif sampling == "4:2:2":
                                            if component["width"] != (flow["frame_width"] / 2) or \
                                                    component["height"] != flow["frame_height"]:
                                                sampling_error = True
                                        elif sampling == "4:2:0":
                                            if component["width"] != (flow["frame_width"] / 2) or \
                                                    component["height"] != (flow["frame_height"] / 2):
                                                sampling_error = True
                                        if sampling_error:
                                            return test.FAIL("Video Flow {} components do not match the expected "
                                                             "dimensions for Sender sampling {}"
                                                             .format(flow["id"], sampling))
                            elif param_components[0] == "width":  # ref: RFC4175
                                if flow["frame_width"] != int(param_components[1]):
                                    return test.FAIL("Width for Sender {} does not match its Flow {}"
                                                     .format(resource["id"], flow["id"]))
                            elif param_components[0] == "height":  # ref: RFC4175
                                if flow["frame_height"] != int(param_components[1]):
                                    return test.FAIL("Height for Sender {} does not match its Flow {}"
                                                     .format(resource["id"], flow["id"]))
                            elif param_components[0] == "depth":  # ref: RFC4175
                                for component in flow["components"]:
                                    if component["bit_depth"] != int(param_components[1]):
                                        return test.FAIL("Bit depth for Sender {} does not match its Flow {}"
                                                         .format(resource["id"], flow["id"]))
                            elif param_components[0] == "colorimetry":  # ref: RFC4175 and ST.2110-20
                                if param_components[1].startswith("BT"):
                                    # RFC4175 uses 'BT709-2', but ST.2110-20 uses 'BT709'
                                    colorimetry_match = param_components[1].split("-")[0]
                                else:
                                    colorimetry_match = param_components[1]
                                if flow["colorspace"] != colorimetry_match:
                                    return test.FAIL("Colorimetry for Sender {} does not match its Flow {}"
                                                     .format(resource["id"], flow["id"]))
                            elif param_components[0] == "interlace":  # ref: RFC4175
                                if "interlace_mode" not in flow or flow["interlace_mode"] == "progressive":
                                    return test.FAIL("Interlace parameter for Sender {} does not match its Flow {}"
                                                     .format(resource["id"], flow["id"]))
                                elif len(param_components) > 1:
                                    return test.FAIL("Interlace parameter for Sender {} incorrectly includes an '='"
                                                     .format(resource["id"]))
                            elif param_components[0] == "top-field-first":  # ref: RFC4175
                                if "interlace_mode" not in flow or flow["interlace_mode"] != "interlaced_tff":
                                    return test.FAIL("Top-field-first parameter for Sender {} does not match its Flow "
                                                     "{}".format(resource["id"], flow["id"]))
                                elif len(param_components) > 1:
                                    return test.FAIL("Top-field-first parameter for Sender {} incorrectly includes an "
                                                     "'='".format(resource["id"]))
                            elif param_components[0] == "segmented":  # ref: ST.2110-20
                                if "interlace_mode" not in flow or flow["interlace_mode"] != "interlaced_psf":
                                    return test.FAIL("Segmented parameter for Sender {} does not match its Flow {}"
                                                     .format(resource["id"], flow["id"]))
                                elif len(param_components) > 1:
                                    return test.FAIL("Segmented parameter for Sender {} incorrectly includes an '='"
                                                     .format(resource["id"]))
                            elif param_components[0] == "exactframerate":  # ref: ST.2110-20
                                if "grain_rate" not in source:
                                    return test.FAIL("No grain_rate found for Source {} associated with Sender {}"
                                                     .format(source["id"], resource["id"]))
                                if "grain_rate" in flow:
                                    flow_rate = self.exactframerate(flow["grain_rate"])
                                    if param_components[1] != flow_rate:
                                        return test.FAIL("Exactframerate for Sender {} does not match its Flow {}"
                                                         .format(resource["id"], flow["id"]))
                                else:
                                    source_rate = self.exactframerate(source["grain_rate"])
                                    if param_components[1] != source_rate:
                                        return test.FAIL("Exactframerate for Sender {} does not match its Source {} "
                                                         "and is not overridden by the Flow"
                                                         .format(resource["id"], source["id"]))
                            elif param_components[0] == "TCS":  # ref: ST.2110-20
                                if "transfer_characteristic" not in flow and param_components[1] != "SDR":
                                    return test.FAIL("Transfer characteristic is missing from Flow attributes")
                                elif "transfer_characteristic" in flow and \
                                        flow["transfer_characteristic"] != param_components[1]:
                                    return test.FAIL("TCS parameter for Sender {} does not match its Flow {}"
                                                     .format(resource["id"], flow["id"]))
                    elif fmtp and flow["media_type"].startswith("audio/L"):
                        for param in fmtp.group(1).split(";"):
                            param_components = param.strip().split("=")
                            if param_components[0] == "channel-order":  # ref: ST.2110-30
                                if self.channel_order(source["channels"]) != param_components[1]:
                                    return test.FAIL("Channel-order parameter for Sender {} does not match its Source "
                                                     "{}".format(resource["id"], source["id"]))
                    elif fmtp and flow["media_type"] == "video/smpte291":
                        for param in fmtp.group(1).split(";"):
                            param_components = param.strip().split("=")
                            if param_components[0] == "DID_SDID":  # ref: RFC8331
                                did, sdid = param_components[1].strip("{").rstrip("}").split(",")
                                if "DID_SDID" not in flow:
                                    return test.FAIL("No DID_SDID found for Flow {} associated with Sender {}"
                                                     .format(flow["id"], resource["id"]))
                                found_match = False
                                for did_sdid in flow["DID_SDID"]:
                                    if int(did_sdid["DID"], 16) == int(did, 16) and \
                                            int(did_sdid["SDID"], 16) == int(sdid, 16):
                                        found_match = True

                                if not found_match:
                                    return test.FAIL("DID/SDID parameters for Sender {} do not match the Flow {}"
                                                     .format(resource["id"], flow["id"]))
        except KeyError as ex:
            return test.FAIL("Expected attribute not found in IS-04 resource: {}".format(ex))

        return test.PASS()

    def test_16(self, test):
        """IS-05 transportfile optional fmtp parameters match IS-04 Source and Flow"""

        for resource_type in ["senders", "flows", "sources"]:
            valid, result = self.get_is04_resources(resource_type)
            if not valid:
                return test.FAIL(result)

        valid, result = self.get_is05_resources("senders")
        if not valid:
            return test.FAIL(result)

        if len(self.is04_resources["senders"]) == 0:
            return test.UNCLEAR("Could not find any IS-05 Senders to test")

        flow_map = {flow["id"]: flow for flow in self.is04_resources["flows"]}

        for resource in self.is04_resources["senders"]:
            if not resource["transport"].startswith("urn:x-nmos:transport:rtp"):
                continue
            if resource["flow_id"] is None:
                continue

            flow = flow_map[resource["flow_id"]]

            is05_transport_file = self.is05_resources["transport_files"][resource["id"]]
            if is05_transport_file is None:
                return test.FAIL("Unable to download transportfile for Sender {}".format(resource["id"]))

            payload_type = self.rtp_ptype(is05_transport_file)
            if not payload_type:
                return test.FAIL("Unable to locate payload type from rtpmap in SDP file for Sender {}"
                                 .format(resource["id"]))

            sdp_interlace = False
            sdp_top_field_first = False
            sdp_segmented = False
            sdp_tcs = False
            sdp_did_sdid = False

            for sdp_line in is05_transport_file.split("\n"):
                sdp_line = sdp_line.replace("\r", "")
                fmtp = re.search(r"^a=fmtp:{} (.+)$".format(payload_type), sdp_line)
                if fmtp and flow["format"] == "urn:x-nmos:format:video":
                    for param in fmtp.group(1).split(";"):
                        param_components = param.strip().split("=")
                        if param_components[0] == "interlace":  # ref: RFC4175
                            sdp_interlace = True
                        elif param_components[0] == "top-field-first":  # ref: RFC4175
                            sdp_top_field_first = True
                        elif param_components[0] == "segmented":  # ref: ST.2110-20
                            sdp_segmented = True
                        elif param_components[0] == "TCS":  # ref: ST.2110-20
                            sdp_tcs = True
                elif fmtp and flow["media_type"] == "video/smpte291":
                    for param in fmtp.group(1).split(";"):
                        param_components = param.strip().split("=")
                        if param_components[0] == "DID_SDID":  # ref: RFC8331
                            sdp_did_sdid = True

            if "DID_SDID" in flow and not sdp_did_sdid:
                return test.FAIL("Flow for Sender {} indicates DID_SDID parameter but this is missing from its "
                                 "SDP file".format(resource["id"]))
            if "interlace_mode" in flow and flow["interlace_mode"] != "progressive" and not sdp_interlace:
                return test.FAIL("Flow for Sender {} indicates video is interlaced, but this is missing from its "
                                 "SDP file".format(resource["id"]))
            if "interlace_mode" in flow and flow["interlace_mode"] == "interlaced_tff" and not sdp_top_field_first:
                return test.FAIL("Flow for Sender {} indicates video is top-field-first, but this is missing from its "
                                 "SDP file".format(resource["id"]))
            if "interlace_mode" in flow and flow["interlace_mode"] == "interlaced_psf" and not sdp_segmented:
                return test.FAIL("Flow for Sender {} indicates video is segmented, but this is missing from its "
                                 "SDP file".format(resource["id"]))
            if "transfer_characteristic" in flow and flow["transfer_characteristic"] != "SDR" and not sdp_tcs:
                return test.FAIL("Flow for Sender {} indicates video's transfer characteristic, but this is missing "
                                 "from its SDP file".format(resource["id"]))

            # Technically the following are just SDP validation, so could move to sdpoker
            if (sdp_top_field_first or sdp_segmented) and not sdp_interlace:
                return test.FAIL("SDP file for Sender {} indicates top-field-first or segmented, but doesn't indicate "
                                 "interlace".format(resource["id"]))
            if sdp_top_field_first and sdp_segmented:
                return test.FAIL("SDP file for Sender {} indicates top-field-first and segmented at the same time"
                                 .format(resource["id"]))

        return test.PASS()

    def exactframerate(self, grain_rate):
        """Format an NMOS grain rate like the SDP video format-specific parameter 'exactframerate'"""
        d = grain_rate.get("denominator", 1)
        if d == 1:
            return "{}".format(grain_rate.get("numerator"))
        else:
            return "{}/{}".format(grain_rate.get("numerator"), d)

    def rtp_ptype(self, sdp_file):
        """Extract the payload type from an SDP file string"""
        payload_type = None
        for sdp_line in sdp_file.split("\n"):
            sdp_line = sdp_line.replace("\r", "")
            try:
                payload_type = int(re.search(r"^a=rtpmap:(\d+) ", sdp_line).group(1))
            except Exception:
                pass
        return payload_type

    def channel_order(self, channels):
        """Create an ST.2110-30 'channel-order' format-specific parameter value from an NMOS audio source 'channels'"""
        # first, straightforward comma-separated channel symbols (or "?" if omitted)
        symbols = ",".join([_["symbol"] if "symbol" in _ else "?" for _ in channels])

        # second, replace all ST.2110-30 defined groups with their grouping symbol
        GROUPS = [
            ["L,R,C,LFE,Lss,Rss,Lrs,Rrs", "71"],
            ["L,R,C,LFE,Ls,Rs", "51"],
            ["Lt,Rt", "LtRt"],
            ["L,R", "ST"],
            ["M1,M2", "DM"],
            ["M1", "M"]
        ]
        for G in GROUPS:
            symbols = symbols.replace(G[0], G[1])

        # third, replace all other channel symbols with 'U'
        groups = ",".join([_ if _ in [G[1] for G in GROUPS] else "U" for _ in symbols.split(",")])

        # finally, replace all sequences of 'U' with the required undefined grouping symbol
        # and format as per ST.2110-30
        return "SMPTE2110.({})" \
               .format(re.sub(r"U(,U)*", lambda us: "U{:02d}".format(int((len(us.group())+1)/2)), groups))