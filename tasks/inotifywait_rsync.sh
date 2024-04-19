#!/bin/bash

# Create watched directories if they don't exist.
# rsync will fail otherwise.
if [ ! -d $1 ]; then
    mkdir -p $1
fi

# Whach the local output directory and rsync with
# the remote output directory.
while inotifywait -r $1; do
    rsync -avz $1 "$REMOTE_SERVER:$2"
done
