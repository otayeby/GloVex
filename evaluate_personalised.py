import argparse, logging, scipy, itertools, random, sys

import preprocessor

import numpy as np

logging.basicConfig(format='%(asctime)s : %(levelname)s : %(message)s', level=logging.INFO)
logger = logging.getLogger("glovex")

def eval_personalised_dataset_surprise(models, acm, user, log_every=1000, ignore_order=True):
	logger.info("  ** Evaluating dataset..")
	dataset_surps = []
	count = 0
	for id,title,doc,raw_doc in zip(acm.doc_ids, acm.doc_titles, acm.documents, acm.doc_raws):
		if count and count % log_every == 0:
			logger.info("    **** Evaluated "+str(count)+" documents.")
		if len(doc):
			surps = estimate_personalised_document_surprise_pairs(doc, models, acm, user, ignore_order=ignore_order)
			dataset_surps.append({"id": id,"title":title,"raw":raw_doc, "surprises":surps, "surprise": document_surprise(surps)})
		else:
			dataset_surps.append({"id": id,"title":title,"raw":raw_doc, "surprises":[], "surprise": float("inf")})
		count+=1
	logger.info("  ** Evaluation complete.")
	return dataset_surps

def document_surprise(surps, percentile=95):
	if len(surps):
		return np.percentile([x[2] for x in surps], 100-percentile) #note that percentile calculates the highest.
	return float("inf")

#Investigate whether we need to rekey the document rather than the cooc
def estimate_personalised_document_surprise_pairs(doc, models, acm, user, top_n_per_doc = 0, ignore_order=True):
	surps = {}
	for model,fc in zip(models,acm.famcats):
		#rekeyed_cooccurrence = {acm.per_fc_keys_to_all_keys[fc][k]:{acm.per_fc_keys_to_all_keys[fc][k2]:v2 for k2,v2 in v.iteritems()} for k,v in acm.cooccurrence[fc].iteritems()}
		rekeyed_doc = [(acm.all_keys_to_per_fc_keys[fc][k],v) for k,v in doc if k in acm.all_keys_to_per_fc_keys[fc].keys()]
		est_cooc_mat = estimate_document_cooccurrence_matrix(rekeyed_doc,model,acm.cooccurrence[fc])
		document_cooccurrence_to_surprise(surps, fc, rekeyed_doc, est_cooc_mat, acm.word_occurrence[fc], acm.dictionary, acm.per_fc_keys_to_all_keys[fc], acm.docs_per_fc[fc], ignore_order=ignore_order)
	combine_surprise(surps, {fc:f for fc,f in zip(acm.famcats,user)})
	return surps

def combine_surprise(surps, user):
	for w1,w1_pairs in surps.iteritems():
		for w2 in w1_pairs:
			surps[w1][w2].append(("combined",combine_surprise_across_famcats_for_user(surps[w1][w2],user, method="weighted_sum")))

#Different methods for combining the surprise predictions from each famcat. Current method is a placeholder only.
#Note: Need to re-investigate exactly what the reported surprise values are before this can be used properly.
def combine_surprise_across_famcats_for_user(surprises,user, method="weighted_sum"):
	if method == "weighted_sum":
			return sum(s[1]*user[s[0]] for s in surprises)
	else:
		raise NotImplementedError


def word_pair_surprise(w1_w2_cooccurrence, w1_occurrence, w2_occurrence, n_docs, offset = 0.5):
	# Offset is Laplacian smoothing
	w1_w2_cooccurrence = min(min(w1_occurrence,w2_occurrence),max(0,w1_w2_cooccurrence)) #Capped due to estimates being off sometimes
	p_w1_given_w2 = (w1_w2_cooccurrence + offset) / (w2_occurrence + offset)
	p_w1 = (w1_occurrence + offset) / (n_docs + offset)
	return p_w1_given_w2 / p_w1

def extract_document_cooccurrence_matrix(doc, coocurrence):
	cooc_mat = np.zeros([len(doc),len(doc)])
	for i1,i2 in itertools.combinations(range(len(doc)),2):
		d1 = doc[i1][0]
		d2 = doc[i2][0]
		cooc_mat[i1,i2] = coocurrence[d1][d2]
	return cooc_mat

def estimate_word_pair_cooccurrence(wk1, wk2, model, cooccurrence, offset = 0.5):
	# take dot product of vectors
	cooc = np.dot(model.W[wk1],model.ContextW[wk2]) + model.b[wk1] + model.ContextB[wk2]
	# correct for the rare feature scaling described in https://nlp.stanford.edu/pubs/glove.pdf
	try:
		actual_cooc = cooccurrence[wk1][wk2]
	except KeyError:
		actual_cooc = offset
	if actual_cooc < model.x_max:
		cooc *= 1.0/pow(actual_cooc / model.x_max,model.alpha)
	return cooc[0]

def estimate_document_cooccurrence_matrix(doc, model, cooccurrence):
	cooc_mat = np.zeros([len(doc),len(doc)])
	for i1,i2 in itertools.combinations(range(len(doc)),2):
		d1 = doc[i1][0]
		d2 = doc[i2][0]
		cooc_mat[i1,i2] = estimate_word_pair_cooccurrence(d1, d2, model, cooccurrence)
	return np.triu(np.exp(cooc_mat), k=1)

def top_n_surps_from_doc(doc, model, cooccurrence, word_occurrence, dictionary, n_docs, top_n = 10):
	if len(doc):
		est_cooc_mat = estimate_document_cooccurrence_matrix(doc,model,cooccurrence)
		surps = document_cooccurrence_to_surprise(doc, est_cooc_mat, word_occurrence, dictionary, n_docs)
		surps.sort(key = lambda x: x[2], reverse=False)
		return surps[:min(top_n,len(surps))]
	else:
		return []

#Returns an ordered list of most-to-least surprising word combinations as (w1,w2,surprise) tuples
def document_cooccurrence_to_surprise(surps, fc, doc, cooc_mat, word_occurrence, dictionary, key_map, n_docs, ignore_order=True):
	for i1,i2 in zip(*np.triu_indices(cooc_mat.shape[0],k=1,m=cooc_mat.shape[1])):
		w1 = doc[i1][0]
		w2 = doc[i2][0]
		if not w1 == w2 and w1 in key_map.keys() and w2 in key_map.keys(): #if the words aren't in this famcat's model, then don't make any predictions on them.
			s = word_pair_surprise(cooc_mat[i1,i2], word_occurrence[dictionary[key_map[w1]]], word_occurrence[dictionary[key_map[w2]]], n_docs)
			if key_map[w1] not in surps.keys():
				surps[key_map[w1]] = {key_map[w2]:[(fc,s)]}
			elif key_map[w2] not in surps[key_map[w1]].keys():
				surps[key_map[w1]][key_map[w2]] = [(fc,s)]
			else:
				surps[key_map[w1]][key_map[w2]].append((fc,s))

#Return the surprises from the given list that have the most similar feature word (i.e. w1) to the one in the given surp.
def most_similar_features(surp, surp_list, model, dictionary, n = 10):
	results = []
	for surp2 in surp_list:
		if not surp[:2] == surp2[:2]:
			surp_feat = model.W[dictionary.token2id[surp[0]]]
			surp2_feat = model.W[dictionary.token2id[surp2[0]]]
			results.append([surp,surp2,scipy.spatial.distance.euclidean(surp_feat, surp2_feat)])
	results.sort(key = lambda x: x[2])
	if n and n < len(results):
		return results[:n]
	return results

#Return the surprises from the given list that have the most similar context word(s) (i.e. w2) to the one in the given surp.
def most_similar_contexts(surp, surp_list, model, dictionary, n = 10):
	results = []
	for surp2 in surp_list:
		if not surp[:2] == surp2[:2]:
			surp_context = model.W[dictionary.token2id[surp[1]]]
			surp2_context = model.W[dictionary.token2id[surp2[1]]]
			results.append([surp,surp2,scipy.spatial.distance.euclidean(surp_context, surp2_context)])
	results.sort(key = lambda x: x[2])
	if n and n < len(results):
		return results[:n]
	return results

#Return the surprises from the given list that have the most similar vector difference to the one in the given surp.
def most_similar_differences(surp, surp_list, model, dictionary, n = 10):
	results = []
	for surp2 in surp_list:
		if not surp[:2] == surp2[:2]:
			surp_diff = model.W[dictionary.token2id[surp[0]]] -  model.W[dictionary.token2id[surp[1]]]
			surp2_diff = model.W[dictionary.token2id[surp2[0]]] -  model.W[dictionary.token2id[surp2[1]]]
			results.append([surp,surp2,surp_diff,surp2_diff,scipy.spatial.distance.euclidean(surp_diff, surp2_diff)])
	results.sort(key = lambda x: x[4])
	if n and n < len(results):
		return results[:n]
	return results

if __name__ == "__main__":
	parser = argparse.ArgumentParser(description="Evaluate a dataset using a trained GloVex model.")
	parser.add_argument("inputfile", help='The input file path to work with (omit the args and suffix)')
	parser.add_argument("--no_below", default = 0.001, type=float,
						help="Min fraction of documents a word must appear in to be included.")
	parser.add_argument("--no_above", default = 0.75, type=float,
						help="Max fraction of documents a word can appear in to be included.")
	args = parser.parse_args()
	acm = preprocessor.ACMDL_DocReader(args.inputfile,"title", "abstract", "ID")
	acm.preprocess(no_below=args.no_below, no_above=args.no_above)
	models = preprocessor.load_personalised_models(args.inputfile, acm)
	logger.info(" ** Loaded GloVe")

	user = [random.random() for fc in acm.famcats]
	logger.info(" ** Generated fake user familiarity profile: "+", ".join([str(fc)+": "+str(f) for f,fc in zip(user,acm.famcats)]))

	dataset_surps = eval_personalised_dataset_surprise(models, acm, user, top_n_per_doc=25)
	dataset_surps.sort(key = lambda x: x["surprise"])
	unique_surps = set((p for s in dataset_surps for p in s["surprises"]))
	for doc in dataset_surps[:10]:
		print doc["id"]+":", doc["title"]
		print "  ** 95th percentile surprise:",doc["surprise"]
		print "  ** Abstract:",doc["raw"]
		print "  ** Surprising pairs:",doc["surprises"]
		most_similar = most_similar_differences(doc["surprises"][0],unique_surps,model, acm.dictionary)
		print "  ** Most similar to top surprise:("+str(doc["surprises"][0])+")"
		for pair in most_similar:
			print "    ** ",pair[4],":",pair[1]
		print


