# lightweight container
FROM python:3.11-slim

WORKDIR /app

# copy project files
COPY . /app

# install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# expose flask port (for Maxy -> this service)
EXPOSE 5000

# run the python app
CMD ["python", "app.py"]
