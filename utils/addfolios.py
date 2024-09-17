#!/usr/bin/python
# -*- coding: utf-8 -*-
""" 
Add folios to segments data
"""

import re
import os
import json
import csv

segments_dir = 'segments/'
newsegments_dir = 'newsegments/'


for root, dirs, files in os.walk(segments_dir):
    for file in files:
            filename = file[:-4]
            outputfile = open(newsegments_dir+filename+'.json', 'w', encoding='utf8')
            newlist = []
            with open(segments_dir+file) as segments_file:
                tsv_file = csv.reader(segments_file, delimiter="\t")
                folionumber = '0'
                for line in tsv_file:
                    newline = {}
                    if line[0].startswith('PA'):
                        segnr = line[0]
                        num = segnr.split(":")[1].split(".")[0].split("_")[0]
                        if re.search(r"^(PA_anya|PA_tika|PA_atk)", segnr):
                            if num.endswith('0'):
                                folionumber = num
                        else:
                            folionumber = num
                        newline["segmentnr"] = segnr
                        newline["original"] = line[1]
                        newline["analyzed"] = line[2]
                        newline["folio"] = folionumber
                        newlist.append(newline)
                    else:
                        continue
                
            outputfile.write(json.dumps(newlist, ensure_ascii=False, indent=4))
            outputfile.close()


