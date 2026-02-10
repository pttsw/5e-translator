find output -type f -name "*.csv" -exec mv {} ./terms-bak/ \;
find output -type f ! -name "*.json" -delete
find output -type f -name "*failed_jobs.json" -delete
cp -r output/* ../unearthed-arcana/
cd ../unearthed-arcana/
npm run build