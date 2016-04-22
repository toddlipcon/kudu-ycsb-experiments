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

import glob
import logging
import os
import shutil
import subprocess
import sys
import time
import urllib2

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
CONFIGS_DIR = os.path.join(BASE_DIR, "configs")

YCSB_EXPORTER_FLAGS= ["-p", "exporter=com.yahoo.ycsb.measurements.exporter.JSONArrayMeasurementsExporter"]
YCSB_DIR = os.path.join(BASE_DIR, "..")
YCSB = os.path.join(YCSB_DIR, "bin", "ycsb")

DATA_DIRS = glob.glob("/data/*/todd")

RECORD_COUNT = 100 * 1000 * 1000
OPERATION_COUNT = RECORD_COUNT
MAX_EXECUTION_TIME = 60 * 30 # 30m
DEFAULT_THREADS = 16

LOAD_SYNC_OPS = bool(int(os.environ.get("LOAD_SYNC_OPS", "1")))

class Experiment:
  def __init__(self, exp_id, config_name):
    self.exp_id = exp_id
    self.config_name = config_name

  @property
  def exp_config_dir(self):
    return os.path.join(CONFIGS_DIR, self.config_name)

  @property
  def base_config_dir(self):
    return os.path.join(BASE_DIR, "base-configs")

  @property
  def results_dir(self):
    return os.path.join(BASE_DIR, "results", self.exp_id, self.config_name)
    
  @property
  def log_dir(self):
    return os.path.join(self.results_dir, "logs")

def start_servers(exp):
  if not os.path.exists(exp.log_dir):
    os.makedirs(exp.log_dir)
  logging.info("Starting servers for config dir %s" % exp.exp_config_dir) 
  ts_proc = subprocess.Popen(["kudu-tserver",
      "--flagfile", os.path.join(exp.base_config_dir, "ts.flags"),
      "--flagfile", os.path.join(exp.exp_config_dir, "ts.flags"),
      "--log_dir", exp.log_dir],
      stderr=subprocess.STDOUT,
      stdout=file(os.path.join(exp.log_dir, "ts.stderr"), "w"))
  master_proc = subprocess.Popen(["kudu-master",
      "--flagfile", os.path.join(exp.base_config_dir, "master.flags"),
      "--flagfile", os.path.join(exp.exp_config_dir, "master.flags"),
      "--log_dir", exp.log_dir],
      stderr=subprocess.STDOUT,
      stdout=file(os.path.join(exp.log_dir, "master.stderr"), "w"))
  return (master_proc, ts_proc)


def stop_servers():
  subprocess.call(["pkill", "kudu-tserver"])
  subprocess.call(["pkill", "kudu-master"])


def remove_data():
  for d in DATA_DIRS:
    rmr(os.path.join(d, "ts"))
    rmr(os.path.join(d, "master"))


def rmr(dir):
    if os.path.exists(dir):
      logging.info("Removing ts and master data from %s" % dir)
      shutil.rmtree(dir)


def run_ycsb(exp, phase, workload, threads=DEFAULT_THREADS, sync_ops=True):
  logging.info("Running YCSB %s-%s for config %s" % (phase, workload, exp.config_name))
  results_json = os.path.join(exp.results_dir, "ycsb-%s-%s.json" % (phase, workload))
  argv = [YCSB, phase, "kudu",
       "-P", os.path.join(YCSB_DIR, "workloads", workload),
       "-p", "kudu_table_num_replicas=1",
       "-p", "recordcount=%d" % RECORD_COUNT,
       "-p", "operationcount=%d" % OPERATION_COUNT,
       "-s",
       "-threads", str(threads)] + \
       YCSB_EXPORTER_FLAGS + \
       ["-p", "exportfile=%s" % results_json]
  if phase != "load":
    argv += ["-p", "maxexecutiontime=%s" % MAX_EXECUTION_TIME]
  if sync_ops:
    argv += ["-p", "kudu_sync_ops=true"]

  subprocess.check_call(argv,
       stdout=file(os.path.join(exp.results_dir, "ycsb-%s-%s.log" % (phase, workload)), "w"),
       stderr=subprocess.STDOUT,
       cwd=YCSB_DIR)

def dump_ts_info(exp, suffix):
  for page, fname in [("rpcz", "rpcz"),
               ("metrics?include_raw_histograms=1", "metrics"),
               ("mem-trackers?raw", "mem-trackers")]:
    fname = "%s-%s.txt" % (fname, suffix)
    dst = file(os.path.join(exp.results_dir, fname), "w")
    shutil.copyfileobj(urllib2.urlopen("http://localhost:8050/" + page), dst)

def run_experiment(exp):
  logging.info("Running experiment %s" % exp.config_name)
  stop_servers()
  remove_data()
  start_servers(exp)
  run_ycsb(exp, "load", "workloada", sync_ops=LOAD_SYNC_OPS)
  dump_ts_info(exp, "after-load")
  run_ycsb(exp, "run", "workloadc")
  dump_ts_info(exp, "after-c")
  run_ycsb(exp, "run", "workloada")
  dump_ts_info(exp, "final")
  stop_servers()

def run_all_configs():
  exp_id = os.environ.get("EXPERIMENT_ID", time.strftime("%Y%m%d-%H%M%S"))
  for config_name in os.listdir(CONFIGS_DIR):
    exp = Experiment(exp_id, config_name)
    run_experiment(exp)

if __name__ == "__main__":
  logging.basicConfig(level=logging.INFO)
  if len(DATA_DIRS) == 0:
    raise Exception("Should set DATA_DIRS to point to where ts and master data will go")
    
  run_all_configs()
