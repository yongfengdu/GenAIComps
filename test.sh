
charts_changed=$(find comps/ | \
	grep helm-chart | sed 's/helm-chart.*$/helm-chart\/Chart.yaml/' |sort -u)
echo $charts_changed
compose_changed=$(find comps/llms/deployment/docker_compose/ | \
    grep docker_compose | sed 's/docker_compose.*$/kubernetes\/helm-chart\/Chart.yaml/' |sort -u)
echo $compose_changed
charts_test=$(echo $charts_changed $compose_changed |tr ' ' '\n'|sort -u)
echo $charts_test
