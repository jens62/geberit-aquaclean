#!/usr/bin/env bash

# npx vector-drawable-svg ladywash.xml ladywash.svg

for file in /absolute/path/to/res/drawable/*
do
  echo "$file"
  base_name=$(basename ${file})
  echo "$base_name"
  echo "${base_name%.*}"
  npx vector-drawable-svg "$file" /absolute/path/to/svg/"${base_name%.*}".svg
done