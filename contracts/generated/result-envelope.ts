/* eslint-disable */
/**
 * AUTO-GENERATED — DO NOT EDIT.
 *
 * TypeScript types for the sleap-roots-contracts ResultEnvelope, generated from
 * contracts/schema/result_envelope.schema.json (the pinned JSON Schema) by
 * json-schema-to-typescript. Regenerate with `npm run contracts:gen`; the
 * byte-for-byte drift guard `npm run contracts:check` fails CI if this file and
 * the pinned schema disagree.
 */

/**
 * Pointer to an intermediate artifact (rows in the #2 intermediates table).
 */
export type BlobRef =
  | {
      s3_location: string;
      [k: string]: unknown;
    }
  | {
      box_link: string;
      [k: string]: unknown;
    };
export type Blobs = BlobRef[];
export type ArgoNodeId = string | null;
export type ArgoWorkflowUid = string | null;
export type ContractVersion = string;
export type IdempotencyKey = string;
export type ImageIds = string[];
export type ImagesChecksum = string;
export type ParamHash = string;
export type PipelineRunId = string | null;
export type PredictCodeSha = string;
export type PredictContainerDigest = string;
export type RegistryId = string;
export type RootType = string | null;
export type SleapNnVersion = string;
export type Version = string;
export type WeightsChecksum = string | null;
export type PredictModels = ModelRef[];
export type ProducedAt = string | null;
export type ScanKey = string;
export type TraitsCodeSha = string;
export type TraitsContainerDigest = string;
export type TraitsSleapRootsVersion = string;
export type WorkerRequestId = string | null;
export type Grain = "scan" | "image";
export type Name = string;
export type ScanKey1 = string;
export type Value = number | null;
export type Traits = TraitValue[];

/**
 * One per-scan result: 1 envelope : 1 source row : 1 scan.
 */
export interface ResultEnvelope {
  blobs?: Blobs;
  provenance: Provenance;
  traits: Traits;
  [k: string]: unknown;
}
/**
 * Run provenance; serializes to cyl_trait_sources.metadata jsonb (sub-project #2).
 */
export interface Provenance {
  argo_node_id?: ArgoNodeId;
  argo_workflow_uid?: ArgoWorkflowUid;
  contract_version: ContractVersion;
  idempotency_key?: IdempotencyKey;
  inputs: InputRef;
  params: ResolvedParams;
  pipeline_run_id?: PipelineRunId;
  predict_code_sha: PredictCodeSha;
  predict_container_digest: PredictContainerDigest;
  predict_models: PredictModels;
  produced_at?: ProducedAt;
  scan_key: ScanKey;
  traits_code_sha: TraitsCodeSha;
  traits_container_digest: TraitsContainerDigest;
  traits_sleap_roots_version: TraitsSleapRootsVersion;
  worker_request_id?: WorkerRequestId;
  [k: string]: unknown;
}
/**
 * Pins the input data a run consumed, for reproducibility.
 */
export interface InputRef {
  image_ids: ImageIds;
  images_checksum: ImagesChecksum;
  [k: string]: unknown;
}
/**
 * Fully-resolved run params plus their canonical hash.
 */
export interface ResolvedParams {
  param_hash?: ParamHash;
  values: Values;
  [k: string]: unknown;
}
export interface Values {
  [k: string]: unknown;
}
/**
 * Identity of one model used in a run (FK-able to a future Bloom models table).
 */
export interface ModelRef {
  registry_id: RegistryId;
  root_type?: RootType;
  sleap_nn_version: SleapNnVersion;
  version: Version;
  weights_checksum?: WeightsChecksum;
  [k: string]: unknown;
}
/**
 * One long-format trait row. NaN/inf normalize to None (-> SQL NULL).
 */
export interface TraitValue {
  grain?: Grain;
  name: Name;
  scan_key: ScanKey1;
  value?: Value;
  [k: string]: unknown;
}
