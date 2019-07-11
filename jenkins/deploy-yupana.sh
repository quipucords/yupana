curl -d "{\"text\": \"${OPENSHIFT_PROJECT}/${APP_NAME} (STATUS) - Deploy build started.\"}" -H "Content-Type: application/json" -X POST ${SLACK_QPC_BOTS}

# for ci/qa projects, we need to change the OPENSHIFT_TOKEN to PREPROD_OPENSHIFT_TOKEN
# for stage/prod projects, we need to change the OPENSHIFT_TOKEN to PROD_OPENSHIFT_TOKEN
oc login https://${OPENSHIFT_HOST}:${OPENSHIFT_PORT} --token=${OPENSHIFT_TOKEN}

oc project ${OPENSHIFT_PROJECT}


#Is this a new deployment or an existing app? Decide based on whether the project is empty or not
#If BuildConfig exists, assume that the app is already deployed and we need a rebuild

BUILD_CONFIG=`oc get bc ${APP_NAME} 2>/dev/null | tail -1 | awk '{print $1}'`


if [ -z "$BUILD_CONFIG" ]; then

# no app found so create a new one
  echo "Create a new app"
  curl -d "{\"text\": \"${OPENSHIFT_PROJECT}/${APP_NAME} (STATUS) - Create a new app.\"}" -H "Content-Type: application/json" -X POST ${SLACK_QPC_BOTS}


  oc apply -f ${OPENSHIFT_TEMPLATE_PATH}


  oc new-app --template ${OPENSHIFT_PROJECT}/$(basename ${OPENSHIFT_TEMPLATE_PATH} .yaml) \
      --param NAMESPACE=${OPENSHIFT_PROJECT} \
      --param SOURCE_REPOSITORY_URL=${GIT_URL} \
      --param SOURCE_REPOSITORY_REF=${GIT_BRANCH} \
      --param KAFKA_HOST=${KAFKA_HOST} \
      --param KAFKA_PORT=${KAFKA_PORT} \
      --param KAFKA_NAMESPACE=${KAFKA_NAMESPACE} \
      --param INSIGHTS_HOST_INVENTORY_URL=${INSIGHTS_HOST_INVENTORY_URL} \
      --param MINIMUM_REPLICAS=${MINIMUM_REPLICAS} \
      --param MAXIMUM_REPLICAS=${MAXIMUM_REPLICAS} \
      --param TARGET_CPU_UTILIZATION=${TARGET_CPU_UTILIZATION} \
      --param HOSTS_PER_REQ=${HOSTS_PER_REQ} \
      --param MAX_THREADS=${MAX_THREADS} \
      --param BUILD_VERSION=${BUILD_VERSION} \
      --param PAUSE_KAFKA_FOR_FILE_UPLOAD_SERVICE=${PAUSE_KAFKA_FOR_FILE_UPLOAD_SERVICE} \
      --param POSTGRES_SQL_SERVICE_HOST=${POSTGRES_SQL_SERVICE_HOST} \

  echo "Find build id"
  BUILD_ID=`oc get builds | grep ${APP_NAME} | tail -1 | awk '{print $1}'`
  rc=1
  attempts=75
  count=0
  while [ $rc -ne 0 -a $count -lt $attempts ]; do
    BUILD_ID=`oc get builds | grep ${APP_NAME} | tail -1 | awk '{print $1}'`
    if [ $BUILD_ID == "NAME" ]; then
      count=$(($count+1))
      echo "Attempt $count/$attempts"
      sleep 5
    else
      rc=0
      echo "Build Id is :" ${BUILD_ID}
    fi
  done

  if [ $rc -ne 0 ]; then
    echo "Fail: Build could not be found after maximum attempts"
	curl -d "{\"text\": \"${OPENSHIFT_PROJECT}/${APP_NAME} (FAIL) - Build could not be found after maximum attempts.\"}" -H "Content-Type: application/json" -X POST ${SLACK_QPC_BOTS}
    exit 1
  fi
else

  # Application already exists, just need to start a new build
  echo "App Exists. Triggering application build and deployment"
  curl -d "{\"text\": \"${OPENSHIFT_PROJECT}/${APP_NAME} (STATUS) - App Exists. Triggering application build and deployment.\"}" -H "Content-Type: application/json" -X POST ${SLACK_QPC_BOTS}
  oc process -f ${OPENSHIFT_TEMPLATE_PATH}  \
      --param NAMESPACE=${OPENSHIFT_PROJECT} \
      --param SOURCE_REPOSITORY_URL=${GIT_URL} \
      --param SOURCE_REPOSITORY_REF=${GIT_BRANCH} \
      --param KAFKA_HOST=${KAFKA_HOST} \
      --param KAFKA_PORT=${KAFKA_PORT} \
      --param KAFKA_NAMESPACE=${KAFKA_NAMESPACE} \
      --param INSIGHTS_HOST_INVENTORY_URL=${INSIGHTS_HOST_INVENTORY_URL} \
      --param MINIMUM_REPLICAS=${MINIMUM_REPLICAS} \
      --param MAXIMUM_REPLICAS=${MAXIMUM_REPLICAS} \
      --param TARGET_CPU_UTILIZATION=${TARGET_CPU_UTILIZATION} \
      --param HOSTS_PER_REQ=${HOSTS_PER_REQ} \
      --param MAX_THREADS=${MAX_THREADS} \
      --param BUILD_VERSION=${BUILD_VERSION} \
      --param PAUSE_KAFKA_FOR_FILE_UPLOAD_SERVICE=${PAUSE_KAFKA_FOR_FILE_UPLOAD_SERVICE} \
      --param POSTGRES_SQL_SERVICE_HOST=${POSTGRES_SQL_SERVICE_HOST} \
      | oc apply -f -
  BUILD_ID=`oc start-build ${BUILD_CONFIG} | awk '{print $2}' | sed -e 's/^"//' -e 's/"$//'`
fi

echo "Waiting for build ${BUILD_ID} to start"
curl -d "{\"text\": \"${OPENSHIFT_PROJECT}/${APP_NAME} (STATUS) - Waiting for build ${BUILD_ID} to start.\"}" -H "Content-Type: application/json" -X POST ${SLACK_QPC_BOTS}
rc=1
attempts=25
count=0
while [ $rc -ne 0 -a $count -lt $attempts ]; do
  status=`oc get build ${BUILD_ID} -o jsonpath='{.status.phase}'`
  if [[ $status == "Failed" || $status == "Error" || $status == "Canceled" ]]; then
    echo "Fail: Build completed with unsuccessful status: ${status}"
    curl -d "{\"text\": \"${OPENSHIFT_PROJECT}/${APP_NAME} (FAIL) - Build completed with unsuccessful status: ${status}.\"}" -H "Content-Type: application/json" -X POST ${SLACK_QPC_BOTS}
    exit 1
  fi
  if [ $status == "Complete" ]; then
    echo "Build completed successfully, will test deployment next"
    curl -d "{\"text\": \"${OPENSHIFT_PROJECT}/${APP_NAME} (STATUS) - Build completed successfully, will test deployment next.\"}" -H "Content-Type: application/json" -X POST ${SLACK_QPC_BOTS}
    rc=0
  fi

  if [ $status == "Running" ]; then
    echo "Build started"
    curl -d "{\"text\": \"${OPENSHIFT_PROJECT}/${APP_NAME} (STATUS) - Build started.\"}" -H "Content-Type: application/json" -X POST ${SLACK_QPC_BOTS}
    rc=0
  fi

  if [ $status == "Pending" ]; then
    count=$(($count+1))
    echo "Attempt $count/$attempts"
    sleep 5
  fi
done

# stream the logs for the build that just started
oc logs -f bc/${APP_NAME}



echo "Checking build result status"
rc=1
count=0
attempts=100
while [ $rc -ne 0 -a $count -lt $attempts ]; do
  status=`oc get build ${BUILD_ID} -o jsonpath='{.status.phase}'`
  if [[ $status == "Failed" || $status == "Error" || $status == "Canceled" ]]; then
    echo "Fail: Build completed with unsuccessful status: ${status}"
    curl -d "{\"text\": \"${OPENSHIFT_PROJECT}/${APP_NAME} (FAIL) - Build completed with unsuccessful status: ${status}.\"}" -H "Content-Type: application/json" -X POST ${SLACK_QPC_BOTS}
    exit 1
  fi

  if [ $status == "Complete" ]; then
    echo "Build completed successfully, will test deployment next"
    curl -d "{\"text\": \"${OPENSHIFT_PROJECT}/${APP_NAME} (STATUS) - Build completed successfully, will test deployment next.\"}" -H "Content-Type: application/json" -X POST ${SLACK_QPC_BOTS}
    rc=0
  else
    count=$(($count+1))
    echo "Attempt $count/$attempts"
    sleep 5
  fi
done

if [ $rc -ne 0 ]; then
    echo "Fail: Build did not complete in a reasonable period of time"
    curl -d "{\"text\": \"${OPENSHIFT_PROJECT}/${APP_NAME} (FAIL) - Build did not complete in a reasonable period of time.\"}" -H "Content-Type: application/json" -X POST ${SLACK_QPC_BOTS}
    exit 1
fi

echo "Checking for successful deployment at http://${APP_NAME}-${OPENSHIFT_PROJECT}.${OPENSHIFT_APP_DOMAIN}${APP_READINESS_PROBE}"
set +e
rc=1
count=0
attempts=100
while [ $rc -ne 0 -a $count -lt $attempts ]; do
  CURL_RES=`curl -I --connect-timeout 2 http://${APP_NAME}-${OPENSHIFT_PROJECT}.${OPENSHIFT_APP_DOMAIN}${APP_READINESS_PROBE} 2> /dev/null | head -n 1 | cut -d$' ' -f2`
  if [[ $CURL_RES == "200" ]]; then
    rc=0
    echo "Successful test against http://${APP_NAME}-${OPENSHIFT_PROJECT}.${OPENSHIFT_APP_DOMAIN}${APP_READINESS_PROBE}"
    curl -d "{\"text\": \"${OPENSHIFT_PROJECT}/${APP_NAME} (SUCCESS) - Successful test against http://${APP_NAME}-${OPENSHIFT_PROJECT}.${OPENSHIFT_APP_DOMAIN}${APP_READINESS_PROBE}.\"}" -H "Content-Type: application/json" -X POST ${SLACK_QPC_BOTS}
    break
  fi
  count=$(($count+1))
  echo "Attempt $count/$attempts"
  sleep 5
done
set -e

if [ $rc -ne 0 ]; then
    echo "Failed to access deployment, aborting roll out."
    curl -d "{\"text\": \"${OPENSHIFT_PROJECT}/${APP_NAME} (FAIL) - Failed to access deployment, aborting roll out.\"}" -H "Content-Type: application/json" -X POST ${SLACK_QPC_BOTS}
    exit 1
fi