#!/usr/bin/env python
#
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
This script parses a set of metrics logs output from a tablet server,
and outputs a TSV file including some metrics.

This isn't meant to be used standalone as written, but rather as a template
which is edited based on whatever metrics you'd like to extract. The set
of metrics described below are just a starting point to work from.
"""

import gzip
try:
  import simplejson as json
except:
  import json
import sys

# These metrics will be extracted "as-is" into the TSV.
# The first element of each tuple is the metric name.
# The second is the name that will be used in the TSV header line.
DEFAULT_SIMPLE_METRICS = [
  ("server.generic_current_allocated_bytes", "heap_allocated"),
  ("server.log_block_manager_bytes_under_management", "bytes_on_disk"),
  ("tablet.memrowset_size", "mrs_size"),
  ("server.block_cache_usage", "bc_usage"),
]

# These metrics will be extracted as per-second rates into the TSV.
DEFAULT_RATE_METRICS = [
  ("server.block_manager_total_bytes_read", "bytes_r_per_sec"),
  ("server.block_manager_total_bytes_written", "bytes_w_per_sec"),
  ("server.block_cache_lookups", "bc_lookups_per_sec"),
  ("tablet.rows_inserted", "inserts_per_sec"),
]

# These metrics will be extracted as percentile metrics into the TSV.
# Each metric will generate several columns in the output TSV, with
# percentile numbers suffixed to the column name provided here (foo_p95,
# foo_p99, etc)
DEFAULT_HISTOGRAM_METRICS = [
  ("server.handler_latency_kudu_tserver_TabletServerService_Write", "write"),
  ("tablet.log_append_latency", "log")
]

NaN = float('nan')
UNKNOWN_PERCENTILES = dict(p50=NaN, p95=NaN, p99=NaN, p999=NaN)

def _json_to_map(j):
  """
  Parse the JSON structure in the log into a python dictionary
  keyed by <entity>.<metric name>.

  The entity ID is currently ignored. If there is more than one
  entity of a given type (eg tables), it is undefined which one
  will be reflected in the output metrics.

  TODO: add some way to specify a particular tablet to parse out.
  """
  ret = {}
  for entity in j:
    for m in entity['metrics']:
      ret[entity['type'] + "." + m['name']] = m
  return ret

def _delta(prev, cur, m):
  """ Compute the delta in metric 'm' between two metric snapshots. """
  if m not in prev or m not in cur:
    return 0
  return cur[m]['value'] - prev[m]['value']

def _histogram_stats(prev, cur, m):
  """
  Compute percentile stats for the metric 'm' in the window between two
  metric snapshots.
  """
  if m not in prev or m not in cur or 'values' not in cur[m]:
    return UNKNOWN_PERCENTILES
  prev = prev[m]
  cur = cur[m]

  p_dict = dict(zip(prev.get('values', []),
                    prev.get('counts', [])))
  c_zip = zip(cur.get('values', []),
                    cur.get('counts', []))
  delta_total = cur['total_count'] - prev['total_count']
  if delta_total == 0:
    return UNKNOWN_PERCENTILES
  res = dict()
  cum_count = 0
  for cur_val, cur_count in c_zip:
    prev_count = p_dict.get(cur_val, 0)
    delta_count = cur_count - prev_count
    cum_count += delta_count
    percentile = float(cum_count) / delta_total
    if 'p50' not in res and percentile > 0.50:
      res['p50'] = cur_val
    if 'p95' not in res and percentile > 0.95:
      res['p95'] = cur_val
    if 'p99' not in res and percentile > 0.99:
      res['p99'] = cur_val
    if 'p999' not in res and percentile > 0.999:
      res['p999'] = cur_val
  return res

def _cache_hit_ratio(prev, cur):
  """
  Calculate the cache hit ratio between the two samples.
  If there were no cache hits or misses, this returns NaN.
  """
  delta_hits = _delta(prev, cur, 'server.block_cache_hits_caching')
  delta_misses = _delta(prev, cur, 'server.block_cache_misses_caching')
  if delta_hits + delta_misses > 0:
    cache_ratio = float(delta_hits) / (delta_hits + delta_misses)
  else:
    cache_ratio = NaN
  return cache_ratio



class MetricsLogParser(object):
  def __init__(self, paths,
               simple_metrics=DEFAULT_SIMPLE_METRICS,
               rate_metrics=DEFAULT_RATE_METRICS,
               histogram_metrics=DEFAULT_HISTOGRAM_METRICS):
    self.paths = paths
    self.simple_metrics = simple_metrics
    self.rate_metrics = rate_metrics
    self.histogram_metrics = histogram_metrics

  def column_names(self):
    simple_headers = [header for _, header in self.simple_metrics + self.rate_metrics]
    for _, header in self.histogram_metrics:
      simple_headers.append(header + "_p50")
      simple_headers.append(header + "_p95")
      simple_headers.append(header + "_p99")
      simple_headers.append(header + "_p999")
    return tuple(["time", "cache_hit_ratio"] + simple_headers)

  def __iter__(self):
    prev_data = None

    for path in sorted(self.paths):
      if path.endswith(".gz"):
        f = gzip.GzipFile(path)
      else:
        f = file(path)
      for line in f:
        (_, ts, metrics_json) = line.split(" ", 2)
        ts = float(ts) / 1000000.0
        if prev_data and ts < prev_data['ts'] + 30:
          continue
        data = _json_to_map(json.loads(metrics_json))
        data['ts'] = ts
        if prev_data:
          yield self._process(prev_data, data)
        prev_data = data

  def _process(self, prev, cur):
    """ Process a pair of metric snapshots, returning a tuple. """
    delta_ts = cur['ts'] - prev['ts']
    cache_ratio = _cache_hit_ratio(prev, cur)
    calc_vals = []
    for metric, _ in self.simple_metrics:
      if metric in cur:
        calc_vals.append(cur[metric]['value'])
      else:
        calc_vals.append(NaN)
    calc_vals.extend(_delta(prev, cur, metric)/delta_ts for (metric, _) in self.rate_metrics)
    for metric, _ in self.histogram_metrics:
      stats = _histogram_stats(prev, cur, metric)
      calc_vals.extend([stats['p50'], stats['p95'], stats['p99'], stats['p999']])

    return tuple([(cur['ts'] + prev['ts'])/2, cache_ratio] + calc_vals)

def main(argv):
  p = MetricsLogParser(argv[1:])
  print " ".join(p.column_names())
  for row in p:
    print " ".join(str(x) for x in row)

if __name__ == "__main__":
  main(sys.argv)
