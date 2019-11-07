#!/usr/bin/env python
# -*- coding: utf-8 -*-

#
#  Copyright 2019 The FATE Authors. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.

from arch.api.utils import log_utils
from federatedml.feature.feature_selection.filter_base import BaseFilterMethod
from federatedml.feature.feature_selection.selection_params import SelectionParams
from federatedml.feature.hetero_feature_binning.base_feature_binning import BaseHeteroFeatureBinning
from federatedml.param.feature_selection_param import IVValueSelectionParam
from federatedml.util import consts

LOGGER = log_utils.getLogger()


class IVValueSelectFilter(BaseFilterMethod):
    """
    filter the columns if all values in this feature is the same

    """

    def __init__(self, filter_param: IVValueSelectionParam):
        super().__init__(filter_param)
        self.binning_obj: BaseHeteroFeatureBinning = None
        self.fit_local = False
        self.transfer_variable = None

    def set_transfer_variable(self, transfer_variable):
        self.transfer_variable = transfer_variable

    def set_binning_obj(self, binning_model):
        if binning_model is None:
            raise ValueError("To use iv filter, binning module should be called and setup in 'isomatric_model'"
                             " input for feature selection.")
        self.binning_obj = binning_model


class Guest(IVValueSelectFilter):
    def __init__(self, filter_param: IVValueSelectionParam):
        super().__init__(filter_param)
        self.host_thresholds = None
        self.host_selection_inner_params = []

    def _parse_filter_param(self, filter_param):
        self.value_threshold = filter_param.value_threshold
        self.host_thresholds = filter_param.host_thresholds
        self.fit_local = filter_param.fit_local

    def set_host_party_ids(self, host_party_ids):
        if self.host_thresholds is None:
            self.host_thresholds = [self.value_threshold for _ in range(len(host_party_ids))]
        else:
            try:
                assert len(host_party_ids) == len(self.host_thresholds)
            except AssertionError:
                raise ValueError("Iv value filters param host_threshold set error."
                                 " The length should match host party numbers ")

    def fit(self, data_instances):
        self._sync_select_cols()
        self.selection_param = self.__unilateral_fit(self.binning_obj.binning_obj,
                                                     self.value_threshold,
                                                     self.selection_param)
        for host_id, host_threshold in enumerate(self.host_thresholds):
            self.__unilateral_fit(self.binning_obj.host_results[host_id],
                                  self.host_thresholds[host_id],
                                  self.host_selection_inner_params[host_id])

        self._sync_select_results()
        return self

    def __unilateral_fit(self, binning_model, threshold, selection_param):
        for col_name, col_results in binning_model.bin_results.all_cols_results.items():
            iv = col_results.iv
            if iv > threshold:
                selection_param.add_left_col_name(col_name)
                selection_param.add_feature_value(col_name, iv)
        return selection_param

    def _sync_select_cols(self):
        host_select_col_names = self.transfer_variable.host_select_cols.get(idx=-1)
        for host_id, select_names in enumerate(host_select_col_names):
            host_inner_param = SelectionParams()
            host_inner_param.set_header(select_names)
            host_inner_param.add_select_col_names(select_names)
            self.host_selection_inner_params.append(host_inner_param)

    def _sync_select_results(self):
        for host_id, host_select_results in enumerate(self.host_selection_inner_params):
            self.transfer_variable.result_left_cols.remote(host_select_results.left_col_names,
                                                           role=consts.HOST,
                                                           idx=host_id)


class Host(IVValueSelectFilter):
    def _parse_filter_param(self, filter_param):
        self.fit_local = False

    def fit(self, data_instances):
        self._sync_select_cols()
        self._sync_select_results()

    def _sync_select_cols(self):
        encoded_names = self.binning_obj.bin_inner_param.encode_col_name_list(self.selection_param.select_col_names)
        self.transfer_variable.host_select_cols.remote(encoded_names,
                                                       role=consts.GUEST,
                                                       idx=0)

    def _sync_select_results(self):
        left_cols_names = self.transfer_variable.result_left_cols.get(idx=0)
        for col_name in left_cols_names:
            self.selection_param.add_left_col_name(col_name)