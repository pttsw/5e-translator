mv output -type f -name "*.csv" ./terms-bak/
find output -type f ! -name "*.json" -delete
find output -type f -name "*failed_jobs.json" -delete
# cp -r output/plu/* ../plutonium-parser/plutonium-cn/data/
rm -rf ../5etools-mirror-2.github.io/data/*
cp -r output/* ../5etools-mirror-2.github.io/data/
cp /data/5etools-mirror-2.github.io/data-bak/spells/index.json /data/5etools-mirror-2.github.io/data/spells/
cp /data/5etools-mirror-2.github.io/data-bak/spells/fluff-index.json /data/5etools-mirror-2.github.io/data/spells/
cp /data/5etools-mirror-2.github.io/data-bak/bestiary/index.json /data/5etools-mirror-2.github.io/data/bestiary/
cp /data/5etools-mirror-2.github.io/data-bak/bestiary/fluff-index.json /data/5etools-mirror-2.github.io/data/bestiary/
cp /data/5etools-mirror-2.github.io/data-bak/class/index.json /data/5etools-mirror-2.github.io/data/class/
cp /data/5etools-mirror-2.github.io/data-bak/class/fluff-index.json /data/5etools-mirror-2.github.io/data/class/
# cd ../homebrew/
# npm run build