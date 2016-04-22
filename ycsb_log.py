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

import re
import numpy

# An example line looks like:
# 2016-04-14 16:16:57:156 10 sec: 3855808 operations; 385580.8 current ops/sec; est completion in 4 minutes [INSERT: Count=3855972, Max=387583, Min=5, Avg=34.22, 90=12, 99=392, 99.9=3657, 99.99=9343] 
TPUT_RE = re.compile(r'(\d+) sec:.*?([\d.]+) current ops/sec')
OP_STATS_RE = re.compile(r'\[([A-Z]+): (.+?)\]')
KV_RE = re.compile(r'([\w\d\.]+)=([\d\.]+)')

class YcsbLogParser(object):
  """ Class which parses a YCSB log file and yields a table. """
  def __init__(self, log_path):
    self.rows = []
    for line in file(log_path, "r"):
      m = TPUT_RE.search(line)
      if not m:
        continue
      row = {}
      row['time'], row['tput'] = m.groups()
      for m in OP_STATS_RE.finditer(line):
        op_type, kvs = m.groups()
        for kv_match in KV_RE.finditer(kvs):
          k, v = kv_match.groups()
          key = '%s_%s' % (op_type, k)
          row[key] = v
      self.rows.append(row)

    # Determine the set of column names
    self.col_names = set()
    for row in self.rows:
      self.col_names.update(row.iterkeys())
    self.col_names = sorted(list(self.col_names))

  def column_names(self):
    return self.col_names

  def as_numpy_array(self):
    a = numpy.empty([len(self.rows)],
                    dtype=[(name, float) for name in self.col_names])
    for i, row in enumerate(self.rows):
      t = tuple([float(row.get(c, "NaN")) for c in self.col_names])
      a[i] = t
    return a
