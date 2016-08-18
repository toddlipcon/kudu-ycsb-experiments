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

import copy
import logging
import os
import shutil
import subprocess
import sys
import time
import urllib2
import yaml

BASE_DIR = os.path.abspath(os.path.dirname(__file__))

SETUP_YAML_PATH = os.path.join(BASE_DIR, "setup.yaml")

YCSB_EXPORTER_FLAGS= ["-p", "exporter=com.yahoo.ycsb.measurements.exporter.JSONArrayMeasurementsExporter"]
YCSB_DIR = os.path.join(BASE_DIR, "..")
YCSB = os.path.join(YCSB_DIR, "bin", "ycsb")

class Experiment:
  def __init__(self, dimensions, config):
    self.dimensions = dimensions
    self.config = config

  @property
  def results_dir(self):
    path = os.path.join(BASE_DIR, "results")
    for dim_name, dim_val in sorted(self.dimensions.iteritems()):
      path = os.path.join(path, "%s=%s" % (dim_name, dim_val))
    return path
    
  @property
  def log_dir(self):
    return os.path.join(self.results_dir, "logs")

  def flags(self, config_key):
    return ["--%s=%s" % kvpair for kvpair in self.config[config_key].iteritems()]

def start_servers(exp):
  if not os.path.exists(exp.log_dir):
    os.makedirs(exp.log_dir)
  logging.info("Starting servers...")
  ts_proc = subprocess.Popen(["kudu-tserver"] + exp.flags("ts_flags") + [
      "--log_dir", exp.log_dir],
      stderr=subprocess.STDOUT,
      stdout=file(os.path.join(exp.log_dir, "ts.stderr"), "w"))
  master_proc = subprocess.Popen(["kudu-master"] + exp.flags("master_flags") + [
      "--log_dir", exp.log_dir],
      stderr=subprocess.STDOUT,
      stdout=file(os.path.join(exp.log_dir, "master.stderr"), "w"))
  return (master_proc, ts_proc)

def stop_servers():
  subprocess.call(["pkill", "kudu-tserver"])
  subprocess.call(["pkill", "kudu-master"])


def remove_data():
  for d in DATA_DIRS:
    rmr(d)

def rmr(dir):
    if os.path.exists(dir):
      logging.info("Removing data from %s" % dir)
      shutil.rmtree(dir)


def run_ycsb(exp, phase, workload, sync_ops=0):
  logging.info("Running YCSB %s-%s for config %s" % (phase, workload, exp.dimensions))
  results_json = os.path.join(exp.results_dir, "ycsb-%s-%s.json" % (phase, workload))
  ycsb_opts = exp.config['ycsb_opts']
  argv = [YCSB, phase, "kudu",
       "-P", os.path.join(YCSB_DIR, "workloads", workload),
       "-p", "kudu_table_num_replicas=1",
       "-p", "recordcount=%d" % ycsb_opts['recordcount'],
       "-p", "operationcount=%d" % ycsb_opts['operationcount'],
       "-p", "kudu_sync_ops=%s" % str(sync_ops),
       "-s",
       "-threads", str(ycsb_opts['threads'])] + \
       YCSB_EXPORTER_FLAGS + \
       ["-p", "exportfile=%s" % results_json]
  if phase != "load":
    argv += ["-p", "maxexecutiontime=%s" % ycsb_opts['max_execution_time']]

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
  if os.path.exists(exp.results_dir):
    logging.info("Skipping experiment %s (results dir already exists)" % exp.dimensions)
  logging.info("Running experiment %s" % exp.dimensions)
  stop_servers()
  remove_data()
  start_servers(exp)
  run_ycsb(exp, "load", "workloada",
      sync_ops=int(exp.config['ycsb_opts']['load_sync']))
  dump_ts_info(exp, "after-load")
#  run_ycsb(exp, "run", "workloadc")
#  dump_ts_info(exp, "after-c")
#  run_ycsb(exp, "run", "workloada")
#  dump_ts_info(exp, "final")
  stop_servers()
  remove_data()

def generate_dimension_combinations(setup_yaml):
  combos = [{}]
  for dim_name, dim_values in setup_yaml['dimensions'].iteritems():
    new_combos = []
    for c in combos:
      for dim_val in dim_values:
        new_combo = c.copy()
        new_combo[dim_name] = dim_val
        new_combos.append(new_combo)
    combos = new_combos
  return combos

def load_experiments(setup_yaml):
  combos = generate_dimension_combinations(setup_yaml)
  exps = []
  for c in combos:
    # 'c' is a dictionary like {"dim1": "dim_val1", "dim2": "dim_val2"}.
    # We need to convert it into the actual set of options and flags.
    setup = copy.deepcopy(setup_yaml['base_flags'])
    setup['dimensions'] = c
    for dim_name, dim_val in c.iteritems():
      # Look up the options for the given dimension value.
      # e.g.: {"ts_flags": {"foo", "bar"}}
      dim_val_dict = setup_yaml['dimensions'][dim_name][dim_val]
      for k, v in dim_val_dict.iteritems():
        setup[k].update(v)
    exps.append(Experiment(c, setup)) 
  return exps


def run_all(exps):
  for exp in exps:
    run_experiment(exp)

if __name__ == "__main__":
  logging.basicConfig(level=logging.INFO)
  setup_yaml = yaml.load(file(SETUP_YAML_PATH))
  global DATA_DIRS
  DATA_DIRS = setup_yaml['all_data_dirs']
  exps = load_experiments(setup_yaml)
  run_all(exps)
