#!/usr/bin/env python

# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.
"""
This file contains various utility functions that are useful
from within IPython notebooks trying to analyze YCSB results.

This should be included using '%run utils.py'
"""

import os
import matplotlib
from matplotlib import pyplot as plt
import glob
import numpy
import json
import time
from kudu_metrics_log import MetricsLogParser
from ycsb_log import YcsbLogParser

def parse_ycsb_start_ts(path):
    for line in file(path):
        if "0 sec: " in line:
            ts = line[0:len("2016-04-14 16:16:47")]
            return time.mktime(time.strptime(ts, "%Y-%m-%d %H:%M:%S"))
    raise "Could not find start time in " + path


def load_experiments(paths):
    data = {}
    for e in paths:
        data[os.path.basename(e)] = load_experiment_data(e)    
    return data


def load_experiment_data(experiment):
    exp = {}
    ycsb_log  = "%s/ycsb-load-workloada.log" % experiment
    exp['ycsb_start_ts'] = parse_ycsb_start_ts(ycsb_log)
    exp['ycsb_load_ts'] = YcsbLogParser(ycsb_log).as_numpy_array()
    summary = json.load(file("%s/ycsb-load-workloada.json" % experiment))
    exp['ycsb_load_stats'] = dict(((p['metric'], p['measurement']), p['value']) for p in summary)
    parser = MetricsLogParser(
        glob.glob("%s/logs/*metrics*" % experiment),
        simple_metrics=[
            ("server.generic_current_allocated_bytes", "heap_allocated")],
        rate_metrics=[
            ("tablet.leader_memory_pressure_rejections", "mem_rejections"),
            ("server.block_manager_total_bytes_written", "bytes_written")
        ],
        histogram_metrics=[
            ("tablet.bloom_lookups_per_op", "bloom_lookups")])                                    
    types = [(colname, float) for colname in parser.column_names()]
    exp['ts_metrics'] = numpy.array(
        list(parser),
        dtype=types)
    return exp


def plot_throughput_latency(experiment, graphs=['tput', 'latency']):
    num_subplots = len(graphs)
    
    # Plot the YCSB time series
    data = experiment['ycsb_load_ts']
    stats = experiment['ycsb_load_stats']
    
    overall_tput = stats[('OVERALL', 'Throughput(ops/sec)')]    
    p99 = stats[('INSERT', '99thPercentileLatency(us)')]/1000
    
    fig = plt.figure(figsize=(20, 5 * num_subplots))
    if 'tput' in graphs:
        ax = fig.add_subplot(num_subplots, 1, 1)
        ax.plot(data['time'], data['tput'])
        ax.set_xlabel("Time (seconds)")
        ax.set_ylabel("Throughput (ops/sec)")
        ax.axhline(overall_tput, linestyle="dashed")

    if 'latency' in graphs:
        ax = fig.add_subplot(num_subplots, 1, 2)
        ax.plot(data['time'], data['INSERT_99'] / 1000)
        ax.set_xlabel("Time (seconds)")
        ax.set_ylabel("99th percentile (ms)")
        ax.axhline(p99, linestyle="dashed")
    plt.show()
    
    print "Average throughput:", int(overall_tput), "ops/sec"
    
def plot_ts_metric(experiment, metric, ylabel, divisor=1):
    # Restrict the metrics log to the time range while the load was happening
    tsm = experiment['ts_metrics']
    min_time = experiment['ycsb_start_ts']
    max_time = min_time + max(experiment['ycsb_load_ts']['time'])
    tsm = tsm[tsm['time'] <= max_time]
    tsm = tsm[tsm['time'] >= min_time]
    tsm['time'] -= min_time

    # Plot various interesting tablet server memory usage
    fig = plt.figure(figsize=(20, 5))
    ax = fig.add_subplot(1, 1, 1)
    ax.set_xlabel("Time (seconds)")
    ax.set_ylabel(ylabel)
    ax.plot(tsm['time'], tsm[metric]/divisor)
    plt.show()


matplotlib.rcParams.update({'font.size': 18})
