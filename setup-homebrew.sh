mv output -type f -name "*.csv" ./terms-bak/
find output -type f ! -name "*.json" -delete
find output -type f -name "*failed_jobs.json" -delete
cp -r output/* ../homebrew/
cd ../homebrew/
npm run build