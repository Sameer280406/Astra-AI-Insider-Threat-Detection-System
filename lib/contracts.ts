export type Role = 'platform_admin'|'org_admin'|'soc_manager'|'soc_analyst'|'auditor'|'viewer';
export type Item = {id:string;entityId?:string|null;createdAt?:string;[key:string]:any};
export type Principal={id:string;name:string;email:string;role:Role;organization:{id:string;name:string;slug:string;tier:string};features:Record<string,number|boolean>};
export type ModelPrediction={modelName:string;modelVersion:string;entityId:string;timestamp:string;anomalyScore:number;riskScore:number;confidence:number;classification:'normal'|'suspicious'|'high_risk';threatType:string;featureContributions:{feature:string;value:number;contribution:number;direction:string}[];evidence:{eventId:string;description:string}[];metadata:Record<string,unknown>};
export type Store=Record<string,Item[]>;
