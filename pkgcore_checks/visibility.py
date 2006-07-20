# Copyright: 2006 Brian Harring <ferringb@gmail.com>
# License: GPL2

import os
from pkgcore.util.compatibility import any
from pkgcore.util.demandload import demandload
from pkgcore_checks import base, util, arches
from pkgcore.util.iterables import caching_iter, expandable_chain
from pkgcore.util.lists import stable_unique, iflatten_instance
from pkgcore.util.containers import ProtectedSet
from pkgcore.restrictions import packages, values
from pkgcore.package.atom import atom
demandload(globals(), "pkgcore.util.containers:InvertedContains")
demandload(globals(), "pkgcore.util.xml:escape")


class VisibilityReport(base.template):

	"""Visibility dependency scans.
	Check that at least one solution is possible for a pkg, checking all profiles (defined by arch.list) visibility modifiers per stable/unstable keyword
	"""

	feed_type = base.package_feed
	requires_profiles = True
	uses_caches = True

	vcs_eclasses = ("subversion", "git", "cvs", "darcs")

	def __init__(self, arches=arches.default_arches):
		self.arches = frozenset(x.lstrip("~") for x in arches)
		self.repo = self.profile_filters = None
		self.keywords_filter = None
	
	def start(self, repo, global_insoluable, keywords_filter, profile_filters):
		self.repo = repo
		self.global_insoluable = global_insoluable
		self.keywords_filter = keywords_filter
		self.profile_filters = profile_filters

	def feed(self, pkgset, reporter, feeder):
		# query_cache gets caching_iter partial repo searches shoved into it- reason is simple,
		# it's likely that versions of this pkg probably use similar deps- so we're forcing those
		# packages that were accessed for atom matching to remain in memory.
		# end result is less going to disk
		for pkg in pkgset:
			if any(True for eclass in self.vcs_eclasses if eclass in pkg.data["_eclasses_"]):
				# vcs ebuild that better not be visible
				self.check_visibility_vcs(pkg, reporter)
			self.check_pkg(pkg, feeder.query_cache, reporter)

	def check_visibility_vcs(self, pkg, reporter):
		for key, profile_dict in self.profile_filters.iteritems():
			if not key.startswith("~"):
				continue
			for profile_name, vals in profile_dict.iteritems():
				if vals[3].match(pkg):
					reporter.add_report(VisibleVcsPkg(pkg, key, profile_name))
	

	def check_pkg(self, pkg, query_cache, reporter):
		nonexistant = set()
		for node in iflatten_instance(pkg.depends, atom):
			h = hash(node)
			if h not in query_cache:
				if h in self.global_insoluable:
					nonexistant.add(node)
					# insert an empty tuple, so that tight loops further on don't have to
					# use the slower get method
					query_cache[h] = ()
				else:
					matches = caching_iter(self.repo.itermatch(node))
					if matches:
						query_cache[h] = matches
					elif not node.blocks and not node.category == "virtual":
						nonexistant.add(node)
						query_cache[h] = ()
						self.global_insoluable.add(h)

		if nonexistant:
			reporter.add_report(NonExistantDeps(pkg, "depends", nonexistant))
			nonexistant.clear()

		# force it to be stable, then unstable ordering for an unstable optimization below
		for node in iflatten_instance(pkg.rdepends, atom):
			h = hash(node)
			if h not in query_cache:
				if h in self.global_insoluable:
					nonexistant.add(node)
					query_cache[h] = ()
				else:
					matches = caching_iter(self.repo.itermatch(node))
					if matches:
						query_cache[h] = matches
					elif not node.blocks and not node.category == "virtual":
						nonexistant.add(node)
						query_cache[h] = ()
						self.global_insoluable.add(h)

		if nonexistant:
			reporter.add_report(NonExistantDeps(pkg, "rdepends", nonexistant))
		del nonexistant
		diuse = pkg.depends.known_conditionals
		riuse = pkg.rdepends.known_conditionals
		deval_cache = {}
		reval_cache = {}
		for key in self.keywords_filter:
			if not self.keywords_filter[key].match(pkg):
				continue
			for profile, val in self.profile_filters[key].iteritems():
				virtuals, flags, non_tristate, vfilter, cache, insoluable = val
				masked_status = not vfilter.match(pkg)

				tri_flags = diuse.difference(non_tristate)
				set_flags = diuse.intersection(flags)
				deps = deval_cache.get((tri_flags, set_flags), None)
				if deps is None:
					deps = deval_cache[(tri_flags, set_flags)] = pkg.depends.evaluate_depset(flags, tristate_filter=non_tristate)

				bad = self.process_depset(deps, 
					virtuals, vfilter, cache, insoluable, query_cache)
				if bad:
					reporter.add_report(NonsolvableDeps(pkg, "depends", key, profile, bad, masked=masked_status))

				tri_flags = riuse.difference(non_tristate)
				set_flags = riuse.intersection(flags)
				rdeps = reval_cache.get((tri_flags, set_flags), None)
				if rdeps is None:
					rdeps = reval_cache[(tri_flags, set_flags)] = pkg.rdepends.evaluate_depset(flags, tristate_filter=non_tristate)

				bad = self.process_depset(rdeps,
					virtuals, vfilter, cache, insoluable, query_cache)
				if bad:
					reporter.add_report(NonsolvableDeps(pkg, "rdepends/pdepends", key, profile, bad, masked=masked_status))


	def process_depset(self, depset, virtuals, vfilter, cache, insoluable, query_cache):
		failures = set()
		for required in depset.cnf_solutions():
			if any(True for a in required if a.blocks):
				continue
			if any(True for a in required if hash(a) in cache):
				continue
			for a in required:
				h = hash(a)
				if h in insoluable:
					continue
				if virtuals.match(a):
					cache.add(h)
					break
				elif a.category == "virtual" and h not in query_cache:
					insoluable.add(h)
					continue
				else:
					if any(True for pkg in query_cache[h] if vfilter.match(pkg)):
						cache.add(h)
						break
					else:
						insoluable.add(h)
			else:
				# no matches.  not great, should collect them all
				failures.update(required)
				break
		else:
			# all requireds where satisfied.
			return ()
		return list(failures)

	def finish(self, *a):
		self.repo = self.profile_filters = self.keywords_filter = None


class VisibleVcsPkg(base.Result):
	description = "pkg is vcs based, but visible"
	__slots__ = ("category", "package", "version", "profile", "arch")

	def __init__(self, pkg, arch, profile):
		self.category, self.package, self.version = pkg.category, pkg.package, pkg.fullver
		self.arch = arch.lstrip("~")
		self.profile = profile
	
	def to_str(self):
		return "%s/%s-%s: vcs ebuild visible for arch %s, profile %s" % \
			(self.category, self.package, self.version, self.arch, self.profile)
	
	def to_xml(self):
		return \
"""<check name="%s">
	<category>%s</category>
	<package>%s</package>
	<version>%s</version>
	<arch>%s</arch>
	<profile>%s</profile>
	<msg>vcs based ebuild user accessible</msg>
</check>""" % (self.__class__.__name__, self.category, self.package, self.version,
	self.arch, self.profile)


class NonExistantDeps(base.Result):
	description = "No matches exist for a depset element"
	__slots__ = ("category", "package", "version", "attr", "atoms")
	
	def __init__(self, pkg, attr, nonexistant_atoms):
		self.category = pkg.category
		self.package = pkg.package
		self.version = pkg.fullver
		self.attr = attr
		self.atoms = tuple(str(x) for x in nonexistant_atoms)
		
	def to_str(self):
		return "%s/%s-%s: attr(%s): nonexistant atoms [ %s ]" % \
			(self.category, self.package, self.version, self.attr, ", ".join(self.atoms))

	def to_xml(self):
		return \
"""<check name="%s">
	<category>%s</category>
	<package>%s</package>
	<version>%s</version>
	<msg>%s: nonexistant atoms [ %s ]</msg>
</check>""" % (self.__class__.__name__, self.category, self.package, self.version,
self.attr, escape(", ".join(self.atoms)))


class NonsolvableDeps(base.Result):
	description = "No potential solution for a depset attribute"
	__slots__ = ("category", "package", "version", "attr", "profile", "keyword", 
		"potentials", "masked")
	
	def __init__(self, pkg, attr, keyword, profile, horked, masked=False):
		self.category = pkg.category
		self.package = pkg.package
		self.version = pkg.fullver
		self.attr = attr
		self.profile = profile
		self.keyword = keyword
		self.potentials = tuple(str(x) for x in stable_unique(horked))
		self.masked = masked
		
	def to_str(self):
		s=' '
		if self.keyword.startswith("~"):
			s=''
		if self.masked:
			s = "masked "+s
		return "%s/%s-%s: %s %s%s: unsolvable %s, solutions: [ %s ]" % \
			(self.category, self.package, self.version, self.attr, s, self.keyword, self.profile,
			", ".join(self.potentials))

	def to_xml(self):
		s = ''
		if self.masked:
			s = "masked, "
		return \
"""<check name="%s">
	<category>%s</category>
	<package>%s</package>
	<version>%s</version>
	<profile>%s</profile>
	<keyword>%s</keyword>
	<msg>%snot solvable for %s- potential solutions, %s</msg>
</check>""" % (self.__class__.__name__, self.category, self.package, self.version,
self.profile, self.keyword, s, self.attr, escape(", ".join(self.potentials)))
