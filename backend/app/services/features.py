from statistics import mean,pstdev
class FeaturePipeline:
    version='1.0'
    def build(self,event,history,peers):
        baseline=[float(x.data.get('egressMb',0)) for x in history]
        peer_values=[float(x.data.get('egressMb',0)) for x in peers]
        avg=mean(baseline) if baseline else 100
        peer_mean=mean(peer_values) if peer_values else 100
        peer_std=max(pstdev(peer_values),100) if peer_values else 100
        return {'egress_mb':event.egressMb,'sensitive_files':float(event.sensitiveFiles),'off_hours':float(event.timestamp.hour<7 or event.timestamp.hour>=20),'new_device':float(event.newDevice),'peer_deviation':max(0,(event.egressMb-peer_mean)/peer_std),'baseline_egress':avg,'behavior_drift':event.egressMb-avg,'session_count':float(len(history)+1)}
