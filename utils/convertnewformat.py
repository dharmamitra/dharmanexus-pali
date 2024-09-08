#!/usr/bin/python
# -*- coding: utf-8 -*-
""" 
Convert old json BuddhaNexus metadata files to new format that includes all
incl. folios
"""

import re
import os
import json

# Set base_dir to where the old BN metadata are found
base_dir = 'metadata/' 
files_dir = os.environ['HOME']+'/dharmanexus-pali/'

with open(base_dir+'pli-categories.json','r', encoding='utf8') as catfile:
    categoryfile = json.load(catfile)

with open(base_dir+'pli-collections.json','r', encoding='utf8') as colfile:
    collectionfile = json.load(colfile)

with open(base_dir+'pli-files.json','r', encoding='utf8') as filefile:
    filesfile = json.load(filefile)

outputfile = open(files_dir+'PA-files.json', 'w', encoding='utf8')

outputlist = []

commentaries = r"^(Anya|Tika|Atthakatha)"

for collection in collectionfile:
    for category in collection["categories"]:
        for cat in categoryfile:
            if cat["category"] == category:
                categoryname = cat["categoryname"]
        for file in filesfile:
            if file["category"] == category:
                file["oldfilename"] = file["filename"]
                file["categoryname"] = categoryname
                if not re.search(commentaries, collection["collection"]):
                    file["link2"] = "https://suttacentral.net/"+file["oldfilename"]
                else:
                    file["link"] = "https://tipitaka.org/romn/"
                if not collection["collection"] == "Vinaya":
                    newfilename = re.sub(category, '', file["oldfilename"])
                    file["filename"] = "PA_"+category+"_"+newfilename
                elif file["oldfilename"].endswith('-pm'):
                    file["categoryname"] = "Pātimokkha"
                    file["category"] = "pm"
                    newfilename = re.sub("pli-tv-", '', file["oldfilename"])
                    newfilename = re.sub("-pm", '', newfilename)
                    file["filename"] = "PA_pm_"+newfilename
                    file["textname"] = "Pm "+newfilename.title()
                else:
                    newcategory = re.sub('pli-tv-', '', category)
                    file["category"] = newcategory
                    newfilename = re.sub(category, '', file["oldfilename"]).lstrip('-')
                    file["filename"] = "PA_"+newcategory+"_"+newfilename
                    file["textname"] = newcategory.title()+" "+newfilename.title()

                
                file["collection"] = collection["collection"]

                # Calculating Pali folios
                folios = []
                try:
                    currentfile = open(files_dir+'segments/'+file["filename"]+'.tsv','r', encoding='utf8')

                    segment_keys=[]
                    
                    for line in currentfile:
                        segnr = line.split()[0]
                        if not segnr == 'segmentnr':
                            segment_keys.append(segnr)

                    last_num = ''
                    for segment_key in segment_keys:
                        num = segment_key.split(":")[1].split(".")[0].split("_")[0]
                        if re.search(r"^(PA_anya|PA_tika|PA_atk)", segment_key):
                            if num.endswith('0') and num != last_num:
                                folios.append({"num": num, "segment_nr": segment_key})
                                last_num = num
                            else:
                                continue
                        else:
                            if num != last_num:
                                folios.append({"num": num, "segment_nr": segment_key})
                                last_num = num

                except:
                    print("file not found: "+files_dir+'segments/'+file["filename"]+'.tsv')

                file["folios"] = folios
                outputlist.append(file)

newlist = sorted(outputlist, key=lambda d: d['filenr'])

outputfile.write(json.dumps(newlist, ensure_ascii=False, indent=4))
outputfile.close()

