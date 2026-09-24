@export()
@description('Built-in Azure role definition IDs (GUIDs).')
var roleIds = {
  // Foundry User (formerly Azure AI User, same GUID): Responses API, agent versions, cloud evaluations.
  // M0-verify: Foundry User covers prompt agent create_version.
  foundryUser: '53ca6127-db72-4b80-b1b0-d745d6d5456d'
  // Language PII, Content Safety and Content Understanding (the CU defaults PATCH requires it).
  // M0-verify: whether Foundry User alone covers these calls, so this role can be dropped.
  cognitiveServicesUser: 'a97b65f3-24c7-4388-baec-2e87135dc908'
  searchIndexDataReader: '1407120a-92aa-4202-b7e9-c0e197c71c8f'
  searchIndexDataContributor: '8ebe5a00-799e-43f5-93ac-243d3dce84a7'
  searchServiceContributor: '7ca78c08-252a-4471-8644-bb5ff32d4ba0'
  storageTableDataContributor: '0a9a7e1f-b9d0-4cc4-a60d-0319b160aaa3'
  // The project identity reads traces for trace and continuous evaluations.
  logAnalyticsReader: '73c42c96-874c-492b-b04d-ab87d138a893'
  // Protected Log Analytics tables (GenAI content).
  // M0-verify: this GUID comes from foundry-samples, not the Learn built-in roles list.
  privilegedMonitoringDataReader: 'dbc9c667-e97f-4491-aee6-90b9cf960190'
}
